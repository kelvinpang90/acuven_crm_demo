"""Build one evaluation sample into its own in-memory SQLite database.

Sample format: docs/ai-eval.md section 2, with the rules of section 9 items 3, 5
and 9. Every time is relative to the sample's ``now``: ``<column>_days_ago`` /
``<column>_minutes_ago`` set any time column (negative is the future, date
columns take the UTC date), the bare ``days_ago`` / ``minutes_ago`` set the
record's main time, and sales targets use ``month_offset``. Only ``key`` and the
references to other records are required; every other column has a default.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Numeric
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models import (
    Activity,
    AutocountDocument,
    Base,
    Contact,
    Deal,
    Message,
    SalesTarget,
    Task,
    User,
)

from . import DASHBOARD_FEATURE

SAMPLE_KEYS = {
    "id",
    "covers",
    "now",
    "injection",
    "users",
    "acting_user",
    "contacts",
    "deals",
    "activities",
    "messages",
    "tasks",
    "documents",
    "sales_targets",
    "params",
    "must_not_contain",
}
# Params naming a record by key; they are passed on with the key replaced by its id.
PARAM_REFERENCES = {"contact": "contacts", "deal": "deals", "message": "messages"}


class SampleError(ValueError):
    """A sample that cannot be built; the message always names the sample id."""


@dataclass(frozen=True)
class _Kind:
    section: str
    model: type
    main_time: str
    # sample field -> (referenced section, column, required)
    references: dict[str, tuple[str, str, bool]]
    keyed: bool = False


KINDS = (
    _Kind("users", User, "created_at", {"manager": ("users", "manager_id", False)}, keyed=True),
    _Kind(
        "contacts",
        Contact,
        "created_at",
        {"assigned_to": ("users", "assigned_to", False)},
        keyed=True,
    ),
    _Kind(
        "deals",
        Deal,
        "created_at",
        {
            "contact": ("contacts", "contact_id", True),
            "assigned_to": ("users", "assigned_to", False),
        },
        keyed=True,
    ),
    _Kind(
        "activities",
        Activity,
        "follow_date",
        {
            "deal": ("deals", "deal_id", True),
            "contact": ("contacts", "contact_id", True),
            "user": ("users", "user_id", False),
        },
    ),
    _Kind(
        "messages",
        Message,
        "created_at",
        {
            "contact": ("contacts", "contact_id", True),
            "assigned_to": ("users", "assigned_to", False),
            "sent_by": ("users", "sent_by", False),
        },
    ),
    _Kind(
        "tasks",
        Task,
        "created_at",
        {
            "contact": ("contacts", "contact_id", False),
            "assigned_to": ("users", "assigned_to", False),
        },
    ),
    _Kind(
        "documents",
        AutocountDocument,
        "doc_date",
        {"contact": ("contacts", "contact_id", False)},
    ),
    _Kind("sales_targets", SalesTarget, "created_at", {"user": ("users", "user_id", True)}),
)


def _defaults(section: str, index: int, key: str | None) -> dict[str, Any]:
    label = key or f"{section}-{index + 1}"
    if section == "users":
        return {"name": label, "email": f"{label}@eval.example.test", "password_hash": "eval"}
    if section == "contacts":
        return {"name": label}
    if section == "activities":
        return {"type": "phone"}
    if section == "messages":
        return {
            "channel": "whatsapp",
            "direction": "inbound",
            "sender_id": "eval-sender",
            "recipient_id": "eval-recipient",
            "body": "",
        }
    if section == "tasks":
        return {"title": label}
    if section == "documents":
        return {"doc_type": "invoice", "doc_no": f"EVAL-{index + 1:04d}", "customer_code": "EVAL"}
    return {}


@dataclass
class SeededSample:
    sample_id: str
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]
    ids: dict[str, str]
    now: datetime
    fixed_now: bool
    acting_user_id: str
    params: dict[str, Any] = field(default_factory=dict)

    async def dispose(self) -> None:
        await self.engine.dispose()


def sample_now(sample: dict[str, Any], feature: str) -> tuple[datetime, bool]:
    """Return the sample's "now" as aware UTC, and whether the sample fixed it."""
    sample_id = sample.get("id")
    value = sample.get("now")
    if value is None:
        return datetime.now(timezone.utc), False
    if feature != DASHBOARD_FEATURE:
        raise SampleError(f"样例 {sample_id}：只有 {DASHBOARD_FEATURE} 的样例可以给 now")
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SampleError(f"样例 {sample_id}：now 必须是以 Z 结尾的 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1])
    except ValueError as exc:
        raise SampleError(f"样例 {sample_id}：now 不是合法的时间：{value}") from exc
    if parsed.tzinfo is not None:
        raise SampleError(f"样例 {sample_id}：now 必须是以 Z 结尾的 UTC 时间")
    return parsed.replace(tzinfo=timezone.utc), True


class _Builder:
    def __init__(self, sample: dict[str, Any], now: datetime) -> None:
        self.sample = sample
        self.sample_id = sample["id"]
        self.now = now.astimezone(timezone.utc).replace(tzinfo=None)
        self.keys: dict[str, tuple[str, str]] = {}
        self.acting_user_id = ""

    def fail(self, message: str) -> SampleError:
        return SampleError(f"样例 {self.sample_id}：{message}")

    def records(self, section: str) -> list[Any]:
        items = self.sample.get(section, [])
        if items is None:
            return []
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise self.fail(f"{section} 必须是对象数组")
        return items

    def register_keys(self) -> None:
        for kind in KINDS:
            for index, item in enumerate(self.records(kind.section)):
                key = item.get("key")
                if key is None:
                    if kind.keyed:
                        raise self.fail(f"{kind.section}[{index}] 缺少必填字段 key")
                    continue
                if not isinstance(key, str) or not key:
                    raise self.fail(f"{kind.section}[{index}] 的 key 必须是非空字符串")
                if key in self.keys:
                    raise self.fail(f"key {key} 重复")
                self.keys[key] = (kind.section, str(uuid.uuid4()))

    def resolve(self, section: str, key: Any, where: str) -> str:
        entry = self.keys.get(key) if isinstance(key, str) else None
        if entry is None or entry[0] != section:
            raise self.fail(f"{where} 引用了不存在的 {section} key：{key}")
        return entry[1]

    def shift(self, column: Any, amount: Any, unit: str, where: str) -> Any:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise self.fail(f"{where} 必须是数字")
        moment = self.now - timedelta(**{unit: amount})
        if isinstance(column.type, Date):
            return moment.date()
        if isinstance(column.type, DateTime):
            return moment
        raise self.fail(f"{where} 指向的列 {column.name} 不是时间列")

    def time_field(self, kind: _Kind, name: str) -> tuple[str, str] | None:
        for suffix, unit in (("days_ago", "days"), ("minutes_ago", "minutes")):
            if name == suffix:
                return kind.main_time, unit
            if name.endswith("_" + suffix):
                return name[: -len(suffix) - 1], unit
        return None

    def month(self, offset: Any, where: str) -> dict[str, int]:
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise self.fail(f"{where} 的 month_offset 必须是整数")
        year, month = divmod(self.now.year * 12 + self.now.month - 1 + offset, 12)
        return {"year": year, "month": month + 1}

    def build(self, kind: _Kind, index: int, item: dict[str, Any]) -> Any:
        where = f"{kind.section}[{index}]"
        columns = kind.model.__table__.columns
        reference_columns = {column for _, column, _ in kind.references.values()}
        key = item.get("key")
        values = _defaults(kind.section, index, key)
        times: set[str] = set()
        if key is not None:
            values["id"] = self.keys[key][1]
        else:
            values["id"] = str(uuid.uuid4())
        if kind.section == "sales_targets":
            values.update(self.month(item.get("month_offset", 0), where))

        for name, raw in item.items():
            if name == "key" or (kind.section == "sales_targets" and name == "month_offset"):
                continue
            if name in kind.references:
                section, column, _ = kind.references[name]
                if raw is not None:
                    values[column] = self.resolve(section, raw, f"{where}.{name}")
                continue
            time_field = self.time_field(kind, name)
            if time_field is not None:
                column_name, unit = time_field
                if column_name not in columns:
                    raise self.fail(f"{where}.{name} 指向不存在的列 {column_name}")
                if column_name in times:
                    raise self.fail(f"{where} 的 {column_name} 给了不止一次")
                times.add(column_name)
                values[column_name] = self.shift(columns[column_name], raw, unit, where)
                continue
            if name == "id" or name not in columns or name in reference_columns:
                raise self.fail(f"{where} 有不认识的字段 {name}")
            column = columns[name]
            if isinstance(column.type, (Date, DateTime)) or (
                kind.section == "sales_targets" and name in {"year", "month"}
            ):
                raise self.fail(f"{where}.{name} 是时间字段，请用相对 now 的写法")
            if isinstance(column.type, Numeric) and raw is not None:
                raw = Decimal(str(raw))
            values[name] = raw

        for name, (_, column, required) in kind.references.items():
            if required and values.get(column) is None:
                raise self.fail(f"{where} 缺少必填字段 {name}")
        if kind.section == "activities" and values.get("user_id") is None:
            values["user_id"] = self.acting_user_id

        main = values.get(kind.main_time)
        anchor = values.get("created_at") or (main if isinstance(main, datetime) else self.now)
        for name, column in columns.items():
            if isinstance(column.type, DateTime) and not column.nullable and name not in values:
                values[name] = anchor
        return kind.model(**values)

    def params(self) -> dict[str, Any]:
        params = self.sample.get("params", {})
        if not isinstance(params, dict):
            raise self.fail("params 必须是对象")
        resolved = dict(params)
        for name, section in PARAM_REFERENCES.items():
            if name in resolved:
                resolved[name] = self.resolve(section, resolved[name], f"params.{name}")
        return resolved

    def all_records(self) -> list[Any]:
        unknown = sorted(set(self.sample) - SAMPLE_KEYS)
        if unknown:
            raise self.fail(f"有不认识的键 {', '.join(unknown)}")
        injection = self.sample.get("injection", False)
        if not isinstance(injection, bool):
            raise self.fail("injection 必须是 true 或 false")
        must_not_contain = self.sample.get("must_not_contain", [])
        if not isinstance(must_not_contain, list) or not all(
            isinstance(text, str) and text for text in must_not_contain
        ):
            raise self.fail("must_not_contain 必须是非空字符串的数组")
        self.register_keys()
        self.acting_user_id = self.resolve("users", self.sample.get("acting_user"), "acting_user")
        records = []
        for kind in KINDS:
            for index, item in enumerate(self.records(kind.section)):
                records.append(self.build(kind, index, item))
        return records


async def seed_sample(sample: dict[str, Any], feature: str) -> SeededSample:
    """Seed ``sample`` into a fresh in-memory SQLite with every table created.

    Raises ``SampleError`` naming the sample before any database is created.
    """
    if not isinstance(sample, dict):
        raise SampleError("样例必须是对象")
    sample_id = sample.get("id")
    if not isinstance(sample_id, str) or not sample_id:
        raise SampleError(f"样例缺少 id：{str(sample)[:80]}")
    now, fixed_now = sample_now(sample, feature)
    builder = _Builder(sample, now)
    records = builder.all_records()
    params = builder.params()

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            session.add_all(records)
            await session.commit()
    except Exception:
        await engine.dispose()
        raise
    return SeededSample(
        sample_id=sample_id,
        engine=engine,
        session_maker=session_maker,
        ids={key: record_id for key, (_, record_id) in builder.keys.items()},
        now=now,
        fixed_now=fixed_now,
        acting_user_id=builder.acting_user_id,
        params=params,
    )
