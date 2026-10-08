import secrets
from dataclasses import dataclass
from re import fullmatch
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Connection, Engine, func, select, update
from sqlalchemy.exc import IntegrityError

from core.database import users


class AccessDenied(ValueError):
    pass


@dataclass(frozen=True)
class User:
    id: str
    login: str
    role: str
    active: bool


class Session:
    def __init__(self, auth: "AuthService", user_id: str, version: int) -> None:
        self.auth = auth
        self.user_id: str | None = user_id
        self.version = version

    @property
    def user(self) -> User:
        if self.user_id is None:
            raise AccessDenied("Сеанс завершён. Войдите в приложение снова.")
        with self.auth.engine.connect() as connection:
            row = (
                connection.execute(select(users).where(users.c.id == self.user_id))
                .mappings()
                .first()
            )
        if row is None or not row["active"] or row["session_version"] != self.version:
            raise AccessDenied("Сеанс завершён. Войдите в приложение снова.")
        return User(row["id"], row["login"], row["role"], bool(row["active"]))

    @property
    def is_admin(self) -> bool:
        try:
            return self.user.role == "admin"
        except AccessDenied:
            return False

    def require_admin(self) -> None:
        if self.user.role != "admin":
            raise AccessDenied("Это действие доступно только администратору.")

    def logout(self) -> None:
        self.user_id = None


class AuthService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.hasher = PasswordHasher(type=Type.ID)
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    @staticmethod
    def validate(login: str, password: str, role: str) -> str:
        login = login.strip().casefold()
        if not fullmatch(r"[a-z0-9_.-]{1,64}", login):
            raise ValueError("Логин: от 1 до 64 латинских букв, цифр, точек, дефисов или _.")
        if not 8 <= len(password) <= 1024:
            raise ValueError("Пароль должен содержать от 8 до 1024 символов.")
        if role not in ("user", "admin"):
            raise ValueError("Неизвестная роль учётной записи.")
        return login

    def has_users(self) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(select(users.c.id).limit(1)).first() is not None

    def _insert_user(
        self, connection: Connection, login: str, password_hash: str, role: str
    ) -> User:
        user = User(uuid4().hex, login, role, True)
        try:
            connection.execute(
                users.insert().values(
                    id=user.id,
                    login=login,
                    password_hash=password_hash,
                    role=role,
                    active=1,
                )
            )
        except IntegrityError as error:
            raise ValueError("Этот логин уже занят.") from error
        return user

    def create_initial_admin(self, login: str, password: str) -> User:
        login = self.validate(login, password, "admin")
        password_hash = self.hasher.hash(password)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            if connection.execute(select(users.c.id).limit(1)).first():
                raise ValueError("Первоначальная учётная запись уже создана.")
            return self._insert_user(connection, login, password_hash, "admin")

    def authenticate(self, login: str, password: str) -> Session:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(users).where(users.c.login == login.strip().casefold()))
                .mappings()
                .first()
            )
        password_hash = row["password_hash"] if row is not None else self._dummy_hash
        try:
            if len(password) > 1024:
                raise ValueError("Неверный логин или пароль.")
            self.hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError) as error:
            raise ValueError("Неверный логин или пароль.") from error
        if row is None or not row["active"]:
            raise ValueError("Неверный логин или пароль.")
        if self.hasher.check_needs_rehash(password_hash):
            with self.engine.begin() as connection:
                connection.execute(
                    update(users)
                    .where(
                        users.c.id == row["id"],
                        users.c.password_hash == password_hash,
                    )
                    .values(password_hash=self.hasher.hash(password))
                )
        return Session(self, row["id"], row["session_version"])

    def list_users(self, session: Session) -> tuple[User, ...]:
        self._require_admin(session)
        with self.engine.connect() as connection:
            rows = connection.execute(select(users).order_by(users.c.login)).mappings()
            return tuple(
                User(row["id"], row["login"], row["role"], bool(row["active"])) for row in rows
            )

    def create_user(self, session: Session, login: str, password: str, role: str = "user") -> User:
        self._require_admin(session)
        login = self.validate(login, password, role)
        password_hash = self.hasher.hash(password)
        with self.engine.begin() as connection:
            return self._insert_user(connection, login, password_hash, role)

    def update_user(self, session: Session, user_id: str, *, role: str, active: bool) -> None:
        self._require_admin(session)
        if role not in ("user", "admin") or type(active) is not bool:
            raise ValueError("Неверная роль или состояние учётной записи.")
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            row = connection.execute(select(users).where(users.c.id == user_id)).mappings().first()
            if row is None:
                raise ValueError("Учётная запись не найдена.")
            if (row["role"], bool(row["active"])) == (role, active):
                return
            if row["role"] == "admin" and row["active"] and (role != "admin" or not active):
                count = connection.scalar(
                    select(func.count())
                    .select_from(users)
                    .where(
                        users.c.role == "admin",
                        users.c.active == 1,
                    )
                )
                if count <= 1:
                    raise ValueError("Нельзя отключить последнего действующего администратора.")
            connection.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(
                    role=role,
                    active=int(active),
                    session_version=users.c.session_version + 1,
                )
            )

    def reset_password(self, session: Session, user_id: str, password: str) -> None:
        self._require_admin(session)
        if user_id == session.user.id:
            raise ValueError("Здесь можно сбросить пароль только другой учётной записи.")
        self.validate("valid", password, "user")
        password_hash = self.hasher.hash(password)
        with self.engine.begin() as connection:
            cursor = connection.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(
                    password_hash=password_hash,
                    session_version=users.c.session_version + 1,
                )
            )
            if not cursor.rowcount:
                raise ValueError("Учётная запись не найдена.")

    def _require_admin(self, session: Session) -> None:
        if session.auth is not self:
            raise AccessDenied("Сеанс относится к другой базе пользователей.")
        session.require_admin()
