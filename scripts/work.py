"""CLI состояния проекта: источники, утверждения, варианты, решения.

Работает только с файлами проекта, без сети и вызовов модели.
"""

import argparse
import copy
import hashlib
from collections import deque
import ipaddress
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import summarize_costs

STAGES = ("brief", "research", "options", "strategy", "decision", "followup")
CLAIM_TYPES = {"fact", "hypothesis", "forecast", "unknown"}
SOURCE_KINDS = {"open", "own", "synthetic"}
APPROVALS = {"pending", "approved", "example"}
COST_KINDS = {"model", "search", "tool", "human"}
ACTIONS = {"source", "record", "checkpoint", "pause", "resume", "retract", "cost"}
COLLECTIONS = {
    "source": "sources",
    "claim": "claims",
    "option": "options",
    "decision": "decisions",
}
COLLECTION_KIND = {name: kind for kind, name in COLLECTIONS.items()}
REF_KINDS = {
    "claim": {"source", "claim"},
    "option": {"source", "claim"},
    "decision": {"source", "claim", "option", "decision"},
}
STATUS_BY_KIND = {
    "source": {"active", "retracted", "superseded"},
    "claim": {"active", "stale", "retracted", "superseded"},
    "option": {"active", "stale", "retracted", "superseded"},
    "decision": {"active", "review_required", "retracted", "superseded"},
}
INVALID_REF_STATUSES = {"stale", "retracted", "superseded"}
PAUSED_ALLOWED = {"resume", "cost"}
ATTENTION_STATUSES = ("stale", "review_required")

STATE_NAME = "state.json"
LOCK_NAME = "state.json.lock"
MATERIALS = "materials"
MAX_SOURCE_BYTES = 5 * 1024 * 1024
RECORD_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SECRET_NAME_TOKENS = {
    "key", "token", "secret", "password", "passwd", "auth", "signature",
    "sig", "credential", "credentials", "apikey", "session", "jwt",
    "bearer", "private", "access",
}
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|gho_[A-Za-z0-9]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16})",
    re.IGNORECASE,
)
LOCAL_SUFFIXES = (
    ".localhost", ".local", ".internal", ".lan", ".home", ".corp",
    ".localdomain", ".home.arpa",
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _check_id(value, field="id"):
    if not isinstance(value, str) or not RECORD_ID.fullmatch(value):
        raise ValueError(
            f"{field}: нужен ID из латиницы, цифр, '-' и '_' "
            "(до 64 символов, начинается с буквы)"
        )
    return value


def _req_str(obj, field):
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: нужна непустая строка")
    return value.strip()


def _iso_date(value, field):
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        raise ValueError(f"{field}: нужна дата в формате ISO YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field}: такой даты не существует") from exc
    return value


def _decimal(value, field):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field}: нужно неотрицательное конечное число") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field}: нужно неотрицательное конечное число")
    return result


def _decimal_str(value, field):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    _decimal(text, field)
    return text


def _clean_host(raw):
    if not isinstance(raw, str):
        raise ValueError("allow_hosts: элемент должен быть строкой")
    host = raw.strip().lower().rstrip(".")
    if not host:
        raise ValueError("allow_hosts: пустой хост")
    if len(host) > 253:
        raise ValueError(f"allow_hosts: «{raw}» слишком длинный")
    if not host.isascii():
        raise ValueError(f"allow_hosts: «{raw}» не ASCII — нужен домен punycode")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError(
            f"allow_hosts: «{host}» — IP-адрес, разрешены только доменные имена"
        )
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        raise ValueError(
            f"allow_hosts: «{host}» — локальное имя, нужны публичные домены"
        )
    if "." not in host:
        raise ValueError(f"allow_hosts: «{host}» без точки — нужен домен")
    for label in host.split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[a-z0-9-]+", label)
        ):
            raise ValueError(f"allow_hosts: «{host}» не похоже на домен")
    return host


def _query_has_secret(query):
    for name, value in parse_qsl(query, keep_blank_values=True):
        tokens = {t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t}
        if tokens & SECRET_NAME_TOKENS:
            return True
        if SECRET_VALUE.search(value):
            return True
    return False


def _validate_open_url(url, allow_hosts):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("У источника kind=open нужен url")
    text = url.strip()
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise ValueError(f"Некорректный URL: {exc}") from exc
    if parts.scheme != "https":
        raise ValueError("URL источника open должен начинаться с https://")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL не должен содержать логин или пароль")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"Некорректный порт в URL: {exc}") from exc
    if port is not None and port != 443:
        raise ValueError("Разрешён только стандартный порт 443")
    host = parts.hostname
    if not host:
        raise ValueError("В URL нет хоста")
    host = host.lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP-адрес в URL запрещён — нужно доменное имя")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("Локальный хост в URL запрещён")
    if host not in allow_hosts:
        raise ValueError(
            f"Хост «{host}» не входит в allow_hosts проекта — "
            "добавьте его через init или не используйте источник"
        )
    if _query_has_secret(parts.query):
        raise ValueError(
            "В query-параметрах URL похожи на секреты — уберите их"
        )
    return text


def _state_path(project):
    return project / STATE_NAME


def _materials_dir(project):
    return project / MATERIALS


def _load_state(project):
    path = _state_path(project)
    if os.path.islink(path):
        raise ValueError("state.json — symlink, операция отменена")
    if not path.is_file():
        raise ValueError("В проекте нет state.json — сначала выполните init")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Не читается state.json: {exc}") from exc
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"state.json повреждён: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError("state.json должен содержать JSON-объект")
    return state


class _ProjectLock:
    def __init__(self, project):
        self.path = project / LOCK_NAME
        self.held = False

    def __enter__(self):
        if os.path.islink(self.path):
            raise ValueError("state.json.lock — symlink, операция отменена")
        try:
            fd = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
        except FileExistsError as exc:
            raise ValueError(
                "Проект занят другой операцией (есть state.json.lock). "
                "Если процесс завершился, удалите файл lock вручную"
            ) from exc
        except OSError as exc:
            raise ValueError(f"Не создать lock: {exc}") from exc
        try:
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        finally:
            os.close(fd)
        self.held = True
        return self

    def __exit__(self, *exc):
        if self.held:
            self.held = False
            try:
                os.unlink(self.path)
            except OSError:
                pass
        return False


def _write_state(project, state):
    target = _state_path(project)
    tmp = project / f"{STATE_NAME}.tmp-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)


def _records_map(state):
    result = {}
    for name, kind in COLLECTION_KIND.items():
        for rid, rec in (state.get(name) or {}).items():
            result[rid] = (kind, rec)
    return result


def _lookup(state, rid):
    return _records_map(state).get(rid)


def _ancestors_map(records, rid):
    seen = set()
    stack = [rid]
    while stack:
        current = stack.pop()
        found = records.get(current)
        if not found:
            continue
        _, rec = found
        for ref in rec.get("refs") or []:
            if isinstance(ref, str) and ref not in seen:
                seen.add(ref)
                stack.append(ref)
    seen.discard(rid)
    return seen


def _depends_on_map(records, candidate, target):
    return candidate == target or target in _ancestors_map(records, candidate)


def _fact_supported_map(records, refs):
    seen = set()
    stack = [r for r in refs if isinstance(r, str)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        found = records.get(current)
        if not found:
            continue
        kind, rec = found
        if kind == "source":
            if isinstance(rec, dict) and rec.get("kind") != "synthetic":
                return True
        elif (
            kind == "claim"
            and isinstance(rec, dict)
            and rec.get("type") == "fact"
        ):
            stack.extend(
                r for r in (rec.get("refs") or []) if isinstance(r, str)
            )
    return False


def _deps_all_active_map(records, refs):
    seen = set()
    for ref in refs:
        if not isinstance(ref, str):
            continue
        seen.add(ref)
        seen |= _ancestors_map(records, ref)
    for rid in seen:
        found = records.get(rid)
        if found and isinstance(found[1], dict):
            if found[1].get("status") != "active":
                return False
    return True


def _validate_refs_create(records, rid, own_kind, refs, require_nonempty=False):
    if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
        raise ValueError("refs должен быть списком ID")
    if require_nonempty and not refs:
        raise ValueError("refs не может быть пустым — нужны основания")
    allowed = REF_KINDS[own_kind]
    for ref in refs:
        _check_id(ref, "refs")
        if ref == rid:
            raise ValueError("Запись не может ссылаться на себя")
        found = records.get(ref)
        if not found:
            raise ValueError(f"Ссылка «{ref}» не найдена — сначала создайте её")
        ref_kind, ref_rec = found
        if ref_kind not in allowed:
            raise ValueError(
                f"{own_kind} не может ссылаться на {ref_kind} «{ref}»"
            )
        if isinstance(ref_rec, dict) and ref_rec.get("status") in (
            INVALID_REF_STATUSES
        ):
            raise ValueError(
                f"Ссылка «{ref}» ведёт на недействительную запись "
                f"(статус {ref_rec.get('status')})"
            )
    return list(refs)


def _insert_record(state, kind, rec):
    rid = rec["id"]
    if _lookup(state, rid):
        raise ValueError(f"ID «{rid}» уже занят")
    affected = []
    sup = rec.get("supersedes")
    if sup is not None:
        found = _lookup(state, sup)
        if not found:
            raise ValueError(f"supersedes «{sup}» не найден")
        sup_kind, sup_rec = found
        if sup_kind != kind:
            raise ValueError("supersedes может указывать только на свой тип")
        if sup_rec.get("status") in ("retracted", "superseded"):
            raise ValueError(
                f"Запись «{sup}» уже имеет статус {sup_rec.get('status')}"
            )
        records = _records_map(state)
        for ref in rec.get("refs") or []:
            if isinstance(ref, str) and _depends_on_map(records, ref, sup):
                raise ValueError(
                    f"Новая запись не может ссылаться на «{sup}» "
                    "или его зависимые — они будут помечены"
                )
        sup_rec["status"] = "superseded"
        affected = _invalidate(state, sup)
    state[COLLECTIONS[kind]][rid] = rec
    return affected


def _invalidate(state, root_id):
    dependents = {}
    for name in ("claims", "options", "decisions"):
        for rid, rec in (state.get(name) or {}).items():
            for ref in (rec.get("refs") or []) if isinstance(rec, dict) else []:
                if isinstance(ref, str):
                    dependents.setdefault(ref, []).append(rid)
    seen = {root_id}
    stack = [root_id]
    while stack:
        current = stack.pop()
        for dep in dependents.get(current, []):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    affected = []
    for rid in sorted(seen - {root_id}):
        found = _lookup(state, rid)
        if not found:
            continue
        kind, rec = found
        status = rec.get("status")
        if kind in ("claim", "option") and status == "active":
            rec["status"] = "stale"
            affected.append(rid)
        elif kind == "decision" and status == "active":
            rec["status"] = "review_required"
            affected.append(rid)
    return sorted(seen - {root_id})


def _retract_record(state, rid):
    found = _lookup(state, rid)
    if not found:
        raise ValueError(f"Запись «{rid}» не найдена")
    kind, rec = found
    status = rec.get("status")
    if status in ("retracted", "superseded"):
        raise ValueError(f"Запись «{rid}» уже имеет статус {status}")
    rec["status"] = "retracted"
    affected = _invalidate(state, rid)
    return kind, affected


def _validate_new_claim(state, obj):
    if not isinstance(obj, dict):
        raise ValueError("JSON записи должен быть объектом")
    rid = _check_id(obj.get("id"))
    if _lookup(state, rid):
        raise ValueError(f"ID «{rid}» уже занят")
    ctype = obj.get("type")
    if ctype not in CLAIM_TYPES:
        raise ValueError("type должен быть fact, hypothesis, forecast или unknown")
    text = _req_str(obj, "text")
    records = _records_map(state)
    refs = _validate_refs_create(
        records, rid, "claim", obj.get("refs"),
        require_nonempty=(ctype == "fact"),
    )
    locator = obj.get("locator")
    if ctype == "fact":
        if not isinstance(locator, str) or not locator.strip():
            raise ValueError(
                "У факта (type=fact) нужен locator — точное место в источнике"
            )
    elif locator is not None and not isinstance(locator, str):
        raise ValueError("locator должен быть строкой")
    rec = {
        "id": rid, "type": ctype, "text": text, "refs": refs,
        "status": "active",
    }
    if isinstance(locator, str) and locator.strip():
        rec["locator"] = locator.strip()
    sup = obj.get("supersedes")
    if sup is not None:
        _check_id(sup, "supersedes")
        rec["supersedes"] = sup
    if ctype == "fact" and not _fact_supported_map(records, refs):
        raise ValueError(
            "Факт не может опираться только на синтетические источники — "
            "нужна ссылка на открытый или собственный источник"
        )
    return rec


def _validate_new_option(state, obj):
    if not isinstance(obj, dict):
        raise ValueError("JSON записи должен быть объектом")
    rid = _check_id(obj.get("id"))
    if _lookup(state, rid):
        raise ValueError(f"ID «{rid}» уже занят")
    approach = _req_str(obj, "approach")
    text = _req_str(obj, "text")
    records = _records_map(state)
    refs = _validate_refs_create(
        records, rid, "option", obj.get("refs"), require_nonempty=True,
    )
    objections = obj.get("objections")
    if (
        not isinstance(objections, list)
        or not objections
        or any(not isinstance(x, str) or not x.strip() for x in objections)
    ):
        raise ValueError("objections должен быть непустым списком строк")
    rec = {
        "id": rid, "approach": approach, "text": text, "refs": refs,
        "objections": [x.strip() for x in objections], "status": "active",
    }
    sup = obj.get("supersedes")
    if sup is not None:
        _check_id(sup, "supersedes")
        rec["supersedes"] = sup
    return rec


def _validate_new_decision(state, obj):
    if not isinstance(obj, dict):
        raise ValueError("JSON записи должен быть объектом")
    rid = _check_id(obj.get("id"))
    if _lookup(state, rid):
        raise ValueError(f"ID «{rid}» уже занят")
    text = _req_str(obj, "text")
    records = _records_map(state)
    refs = _validate_refs_create(
        records, rid, "decision", obj.get("refs"), require_nonempty=True,
    )
    option = _check_id(_req_str(obj, "option"), "option")
    owner = _req_str(obj, "owner")
    due = _iso_date(_req_str(obj, "due"), "due")
    metric = _req_str(obj, "metric")
    trigger = _req_str(obj, "trigger")
    approval = obj.get("approval")
    if approval not in APPROVALS:
        raise ValueError("approval должен быть pending, approved или example")
    rec = {
        "id": rid, "text": text, "refs": refs, "option": option,
        "owner": owner, "due": due, "metric": metric, "trigger": trigger,
        "approval": approval, "status": "active",
    }
    if approval == "approved":
        rec["approved_by"] = _req_str(obj, "approved_by")
        rec["approval_ref"] = _req_str(obj, "approval_ref")
        if not _deps_all_active_map(records, refs):
            raise ValueError(
                "У решения есть зависимости не в статусе active "
                "(stale/review_required) — approved невозможен"
            )
    found = records.get(option)
    if not found or found[0] != "option":
        raise ValueError(f"option «{option}» не является записью варианта")
    if option not in refs:
        raise ValueError("Выбранный option должен быть в refs")
    if isinstance(found[1], dict) and found[1].get("status") != "active":
        raise ValueError(f"option «{option}» не активен")
    sup = obj.get("supersedes")
    if sup is not None:
        _check_id(sup, "supersedes")
        rec["supersedes"] = sup
    return rec


def _fact_gate_ok(state):
    for rec in (state.get("claims") or {}).values():
        if (
            isinstance(rec, dict)
            and rec.get("type") == "fact"
            and rec.get("status") == "active"
        ):
            return True
    return False


def _option_gate(state):
    options = [
        rec for rec in (state.get("options") or {}).values()
        if isinstance(rec, dict) and rec.get("status") == "active"
    ]
    if len(options) < 3:
        return False, "активных вариантов меньше трёх"
    approaches = {o.get("approach") for o in options}
    if "status_quo" not in approaches or "close" not in approaches:
        return False, "нет подходов status_quo и/или close"
    if not any(a not in ("status_quo", "close") for a in approaches):
        return False, "нет третьего различного подхода"
    if any(
        not isinstance(o.get("objections"), list) or not o.get("objections")
        for o in options
    ):
        return False, "у варианта пустые objections"
    return True, ""


def _followup_gate_ok(state):
    for rec in (state.get("decisions") or {}).values():
        if (
            isinstance(rec, dict)
            and rec.get("status") == "active"
            and rec.get("approval") in ("approved", "example")
        ):
            return True
    return False


def _material_suffix(orig_name):
    if "." not in orig_name:
        return ""
    suffix = orig_name[orig_name.index("."):]
    suffix = re.sub(r"[^A-Za-z0-9._-]", "-", suffix)
    return suffix[:40]


def _resolve_existing_project(raw):
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise ValueError(f"Проект не найден или это не папка: {raw}")
    return path.resolve()


def _read_source_input(project, file_arg):
    rel = Path(file_arg)
    if rel.is_absolute():
        raise ValueError("--file должен быть относительным путём внутри проекта")
    if not rel.parts or rel.parts == (".",):
        raise ValueError("--file не может указывать на папку проекта")
    candidate = project / rel
    real = Path(os.path.realpath(candidate))
    root = Path(os.path.realpath(project))
    if real != root and root not in real.parents:
        raise ValueError("--file выходит за пределы проекта — отказ")
    if not real.is_file():
        raise ValueError(f"Файл не найден: {file_arg}")
    if real.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Файл больше 5 МиБ — лимит источника")
    with real.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Файл больше 5 МиБ — лимит источника")
    if not data:
        raise ValueError("Файл пустой — регистрировать нечего")
    return data, rel.name


def _store_material(project, target_name, data):
    mdir = _materials_dir(project)
    if os.path.islink(mdir):
        raise ValueError("materials — symlink, операция отменена")
    if os.path.lexists(mdir) and not mdir.is_dir():
        raise ValueError("materials существует и не является папкой")
    mdir.mkdir(exist_ok=True)
    target = mdir / target_name
    if os.path.islink(target):
        raise ValueError(f"materials/{target_name} — symlink, отказ")
    if os.path.lexists(target):
        if not target.is_file():
            raise ValueError(f"materials/{target_name} уже существует")
        existing = hashlib.sha256(target.read_bytes()).hexdigest()
        incoming = hashlib.sha256(data).hexdigest()
        if existing != incoming:
            raise ValueError(
                f"materials/{target_name} уже есть с другим содержимым — "
                "выберите другой ID источника"
            )
        return
    with open(target, "xb") as stream:
        stream.write(data)


def _run_mutation(project, action, key, payload, mutate):
    _check_id(key, "key")
    with _ProjectLock(project):
        state = _load_state(project)
        for event in state.get("events") or []:
            if not isinstance(event, dict) or event.get("key") != key:
                continue
            if event.get("action") == action and event.get("payload") == payload:
                return {
                    "ok": True, "idempotent": True, "action": action,
                    "revision": state.get("revision"),
                }
            raise ValueError(
                f"Ключ «{key}» уже использован с другим содержимым — "
                "возьмите новый ключ"
            )
        if state.get("paused") and action not in PAUSED_ALLOWED:
            raise ValueError(
                "Проект на паузе: доступны только resume и cost, "
                "остальные действия заблокированы"
            )
        new_state = copy.deepcopy(state)
        info = mutate(new_state) or {}
        new_state["revision"] = int(new_state.get("revision") or 0) + 1
        events = new_state.get("events")
        if not isinstance(events, list):
            events = []
            new_state["events"] = events
        events.append({
            "key": key, "action": action, "payload": payload,
            "at": _now(), "revision": new_state["revision"],
        })
        errors, _ = validate_state(new_state, project)
        if errors:
            raise ValueError(
                "Состояние не сохранено: " + " | ".join(errors)
            )
        _write_state(project, new_state)
        return {
            "ok": True, "action": action,
            "revision": new_state["revision"], **info,
        }


def _wrap(result):
    return result, 0


def cmd_init(args):
    goal = (args.goal or "").strip()
    owner = (args.owner or "").strip()
    if not goal:
        raise ValueError("Нужен --goal — вопрос решения")
    if not owner:
        raise ValueError("Нужен --owner — владелец решения")
    hosts = []
    for raw in args.allow or []:
        host = _clean_host(raw)
        if host not in hosts:
            hosts.append(host)
    budget = _decimal_str(args.budget_usd, "budget-usd")
    project = Path(args.project).expanduser()
    if project.exists() and not project.is_dir():
        raise ValueError(f"Путь проекта — не папка: {args.project}")
    resolved = project.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    state_path = resolved / STATE_NAME
    if os.path.lexists(state_path):
        raise ValueError("state.json уже существует — init отказан")
    with _ProjectLock(resolved):
        if os.path.lexists(state_path):
            raise ValueError("state.json уже существует — init отказан")
        for dirname in ("inbox", MATERIALS):
            target = resolved / dirname
            if os.path.islink(target):
                raise ValueError(f"{dirname} — symlink, init отказан")
            if os.path.lexists(target) and not target.is_dir():
                raise ValueError(f"{dirname} существует и не является папкой")
            target.mkdir(exist_ok=True)
        state = {
            "version": 1, "revision": 0, "goal": goal, "owner": owner,
            "stage": "brief", "paused": False,
            "next": "Собрать источники по брифу",
            "allow_hosts": hosts, "budget_usd": budget,
            "sources": {}, "claims": {}, "options": {}, "decisions": {},
            "costs": [], "events": [],
        }
        init_errors, _ = validate_state(state)
        if init_errors:
            raise ValueError("Не удалось создать state: " + "; ".join(init_errors))
        _write_state(resolved, state)
    return {
        "ok": True, "project": str(resolved), "stage": "brief",
        "revision": 0, "allow_hosts": hosts, "budget_usd": budget,
    }, 0


def cmd_source(args):
    project = _resolve_existing_project(args.project)
    data, orig_name = _read_source_input(project, args.file)
    sha = hashlib.sha256(data).hexdigest()
    payload = {
        "id": args.id, "kind": args.kind, "file": args.file,
        "locator": (args.locator or "").strip(), "sha256": sha,
    }
    if args.url:
        payload["url"] = args.url.strip()
    if args.supersedes:
        payload["supersedes"] = args.supersedes

    def mutate(state):
        sid = _check_id(args.id)
        if _lookup(state, sid):
            raise ValueError(f"ID «{sid}» уже занят")
        kind = args.kind
        locator = (args.locator or "").strip()
        if not locator:
            raise ValueError("Нужен --locator — точное место в источнике")
        url = None
        if kind == "open":
            url = _validate_open_url(
                args.url, state.get("allow_hosts") or []
            )
        elif args.url and str(args.url).strip():
            url = str(args.url).strip()
        sup = None
        if args.supersedes:
            sup = _check_id(args.supersedes, "supersedes")
            found = _lookup(state, sup)
            if not found or found[0] != "source":
                raise ValueError("supersedes должен указывать на источник")
            if found[1].get("status") in ("retracted", "superseded"):
                raise ValueError(
                    f"Источник «{sup}» уже имеет статус "
                    f"{found[1].get('status')}"
                )
        target_name = f"{sid}{_material_suffix(orig_name)}"
        rel = f"{MATERIALS}/{target_name}"
        _store_material(project, target_name, data)
        rec = {
            "id": sid, "kind": kind, "locator": locator, "path": rel,
            "sha256": sha, "observed_on": _today(), "status": "active",
        }
        if url:
            rec["url"] = url
        if sup:
            rec["supersedes"] = sup
        affected = _insert_record(state, "source", rec)
        out = {
            "id": sid, "kind": kind, "path": rel, "sha256": sha,
            "observed_on": rec["observed_on"],
        }
        if affected:
            out["affected"] = affected
        return out

    return _wrap(_run_mutation(project, "source", args.key, payload, mutate))


def cmd_record(args):
    project = _resolve_existing_project(args.project)
    try:
        obj = json.loads(args.json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--json не является корректным JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("--json должен содержать JSON-объект")
    kind = args.kind
    payload = {"kind": kind, "data": obj}

    def mutate(state):
        if kind == "claim":
            rec = _validate_new_claim(state, obj)
        elif kind == "option":
            rec = _validate_new_option(state, obj)
        else:
            rec = _validate_new_decision(state, obj)
        affected = _insert_record(state, kind, rec)
        out = {"kind": kind, "id": rec["id"]}
        if affected:
            out["affected"] = affected
        return out

    return _wrap(_run_mutation(project, "record", args.key, payload, mutate))


def cmd_checkpoint(args):
    project = _resolve_existing_project(args.project)
    payload = {"stage": args.stage, "next": args.next}

    def mutate(state):
        if _budget_info(state)["exceeded"]:
            raise ValueError(
                "Известные расходы превышают бюджет, переход остановлен"
            )
        new_stage = args.stage
        current = state.get("stage")
        if new_stage != current:
            index = STAGES.index(new_stage)
            if new_stage == "options" and not _fact_gate_ok(state):
                raise ValueError(
                    "Для перехода к options нужен хотя бы один активный "
                    "факт (type=fact) с непустыми refs"
                )
            if index >= STAGES.index("strategy"):
                ok, reason = _option_gate(state)
                if not ok:
                    raise ValueError(
                        f"Для этапа {new_stage} не хватает вариантов: {reason}"
                    )
            if new_stage == "followup" and not _followup_gate_ok(state):
                raise ValueError(
                    "Для этапа followup нужно решение со статусом "
                    "approved или example (не pending)"
                )
        state["stage"] = new_stage
        state["next"] = args.next
        return {"stage": new_stage, "next": args.next}

    return _wrap(
        _run_mutation(project, "checkpoint", args.key, payload, mutate)
    )


def cmd_pause(args):
    project = _resolve_existing_project(args.project)
    payload = {"next": args.next}

    def mutate(state):
        state["paused"] = True
        state["next"] = args.next
        return {"paused": True, "next": args.next}

    return _wrap(_run_mutation(project, "pause", args.key, payload, mutate))


def cmd_resume(args):
    project = _resolve_existing_project(args.project)

    def mutate(state):
        if not state.get("paused"):
            raise ValueError("Проект не на паузе — resume не нужен")
        state["paused"] = False
        return {
            "paused": False, "stage": state.get("stage"),
            "next": state.get("next"),
        }

    return _wrap(_run_mutation(project, "resume", args.key, {}, mutate))


def cmd_retract(args):
    project = _resolve_existing_project(args.project)
    reason = (args.reason or "").strip()
    if not reason:
        raise ValueError("Нужен --reason — причина отзыва")
    rid = args.id
    payload = {"id": rid, "reason": reason}

    def mutate(state):
        kind, affected = _retract_record(state, rid)
        out = {"id": rid, "kind": kind, "status": "retracted"}
        if affected:
            out["affected"] = affected
        return out

    return _wrap(_run_mutation(project, "retract", args.key, payload, mutate))


def cmd_cost(args):
    project = _resolve_existing_project(args.project)
    usd = _decimal_str(args.usd, "usd")
    minutes = _decimal_str(args.minutes, "minutes")
    note = (args.note or "").strip()
    if not note:
        raise ValueError("Нужен --note — что за расход")
    entry = {
        "kind": args.kind, "usd": usd, "minutes": minutes, "note": note,
    }
    payload = dict(entry)

    def mutate(state):
        costs = state.get("costs")
        if not isinstance(costs, list):
            raise ValueError("costs в state повреждён — не список")
        costs.append(entry)
        return {"entry": dict(entry)}

    return _wrap(_run_mutation(project, "cost", args.key, payload, mutate))


def _budget_info(state):
    costs = state.get("costs") if isinstance(state.get("costs"), list) else []
    try:
        summary = summarize_costs(costs)
    except Exception as exc:
        return {"summary": {"error": str(exc)}, "budget": None, "exceeded": False}
    exceeded = False
    try:
        exceeded = (
            Decimal(summary["known_usd"])
            > _decimal(state.get("budget_usd"), "budget_usd")
        )
    except ValueError:
        pass
    budget = {
        "budget_usd": state.get("budget_usd"),
        "known_usd": summary["known_usd"],
        "unknown_entries": summary["unknown_entries"],
        "total_usd": summary["total_usd"],
        "recorded_minutes": summary["recorded_minutes"],
        "exceeded": exceeded,
    }
    return {"summary": summary, "budget": budget, "exceeded": exceeded}


def cmd_status(args):
    project = _resolve_existing_project(args.project)
    state = _load_state(project)
    info = _budget_info(state)
    counts = {
        name: len(state.get(name) or {})
        for name in ("sources", "claims", "options", "decisions")
    }
    events = state.get("events")
    counts["costs"] = len(state.get("costs") or [])
    counts["events"] = len(events) if isinstance(events, list) else 0
    attention = {name: [] for name in ("stale", "review_required", "retracted", "superseded")}
    for name in ("sources", "claims", "options", "decisions"):
        for rid, rec in (state.get(name) or {}).items():
            if isinstance(rec, dict) and rec.get("status") in attention:
                attention[rec["status"]].append(rid)
    for name in attention:
        attention[name].sort()
    out = {
        "ok": not info["exceeded"], "project": str(project),
        "goal": state.get("goal"), "owner": state.get("owner"),
        "stage": state.get("stage"), "paused": state.get("paused"),
        "next": state.get("next"), "revision": state.get("revision"),
        "summary": info["summary"], "budget": info["budget"],
        "counts": counts,
        "last_events": (events or [])[-5:] if isinstance(events, list) else [],
        "attention": attention,
    }
    code = 0
    if info["exceeded"]:
        out["error"] = "Известные расходы превышают бюджет проекта"
        code = 1
    return out, code


def _record_field_errors(kind, rid, rec, state, errors):
    hosts = state.get("allow_hosts") or []
    if kind == "source":
        if rec.get("kind") not in SOURCE_KINDS:
            errors.append(f"«{rid}»: kind должен быть open, own или synthetic")
        locator = rec.get("locator")
        if not isinstance(locator, str) or not locator.strip():
            errors.append(f"«{rid}»: нужен непустой locator")
        path = rec.get("path")
        if not isinstance(path, str) or not path:
            errors.append(f"«{rid}»: нужен path к файлу материала")
        elif (
            path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
            or path.split("/")[0] != MATERIALS
        ):
            errors.append(f"«{rid}»: path «{path}» вне materials/")
        sha = rec.get("sha256")
        if not isinstance(sha, str) or not HEX64.fullmatch(sha):
            errors.append(f"«{rid}»: sha256 должен быть 64 hex-символа")
        try:
            _iso_date(rec.get("observed_on"), f"«{rid}» observed_on")
        except ValueError as exc:
            errors.append(str(exc))
        url = rec.get("url")
        if rec.get("kind") == "open":
            try:
                _validate_open_url(url, hosts)
            except ValueError as exc:
                errors.append(f"«{rid}»: {exc}")
        elif url is not None and not isinstance(url, str):
            errors.append(f"«{rid}»: url должен быть строкой")
    elif kind == "claim":
        if rec.get("type") not in CLAIM_TYPES:
            errors.append(f"«{rid}»: type должен быть fact/hypothesis/forecast/unknown")
        if not isinstance(rec.get("text"), str) or not rec.get("text", "").strip():
            errors.append(f"«{rid}»: нужен непустой text")
        locator = rec.get("locator")
        if rec.get("type") == "fact":
            if not isinstance(locator, str) or not locator.strip():
                errors.append(f"«{rid}»: у факта нужен locator")
        elif locator is not None and not isinstance(locator, str):
            errors.append(f"«{rid}»: locator должен быть строкой")
    elif kind == "option":
        if not isinstance(rec.get("approach"), str) or not rec.get("approach", "").strip():
            errors.append(f"«{rid}»: нужен approach")
        if not isinstance(rec.get("text"), str) or not rec.get("text", "").strip():
            errors.append(f"«{rid}»: нужен text")
        objections = rec.get("objections")
        if (
            not isinstance(objections, list)
            or not objections
            or any(not isinstance(x, str) or not x.strip() for x in objections)
        ):
            errors.append(f"«{rid}»: objections должен быть непустым списком строк")
    elif kind == "decision":
        if not isinstance(rec.get("text"), str) or not rec.get("text", "").strip():
            errors.append(f"«{rid}»: нужен text")
        for field in ("option", "owner", "metric", "trigger"):
            if not isinstance(rec.get(field), str) or not rec.get(field, "").strip():
                errors.append(f"«{rid}»: нужен непустой {field}")
        try:
            _iso_date(rec.get("due"), f"«{rid}» due")
        except ValueError as exc:
            errors.append(str(exc))
        if rec.get("approval") not in APPROVALS:
            errors.append(f"«{rid}»: approval должен быть pending/approved/example")
        if rec.get("approval") == "approved":
            for field in ("approved_by", "approval_ref"):
                if not isinstance(rec.get(field), str) or not rec.get(field, "").strip():
                    errors.append(f"«{rid}»: approved требует {field}")


def _validate_refs_state(kind, rid, rec, all_records, errors):
    if kind == "source":
        return
    refs = rec.get("refs")
    if not isinstance(refs, list):
        errors.append(f"«{rid}»: refs должен быть списком")
        return
    if not refs and (
        kind in ("option", "decision")
        or (kind == "claim" and rec.get("type") == "fact")
    ):
        errors.append(f"«{rid}»: нужны основания refs")
    allowed = REF_KINDS.get(kind)
    if allowed is None:
        return
    for ref in refs:
        if not isinstance(ref, str) or not RECORD_ID.fullmatch(ref):
            errors.append(f"«{rid}»: ссылка «{ref}» не похожа на ID")
            continue
        if ref == rid:
            errors.append(f"«{rid}»: ссылка на себя")
            continue
        found = all_records.get(ref)
        if not found:
            errors.append(f"«{rid}»: ссылка «{ref}» не найдена")
            continue
        ref_kind, ref_rec = found
        if ref_kind not in allowed:
            errors.append(f"«{rid}»: {kind} не может ссылаться на {ref_kind} «{ref}»")
        elif (
            rec.get("status") == "active"
            and isinstance(ref_rec, dict)
            and ref_rec.get("status") in INVALID_REF_STATUSES
        ):
            errors.append(
                f"«{rid}»: активная запись ссылается на «{ref}» "
                f"со статусом {ref_rec.get('status')}"
            )
    if kind == "decision" and rec.get("status") == "active":
        option = rec.get("option")
        refs_set = {r for r in refs if isinstance(r, str)}
        found = all_records.get(option) if isinstance(option, str) else None
        if not found or found[0] != "option":
            errors.append(f"«{rid}»: option «{option}» не является вариантом")
        elif option not in refs_set:
            errors.append(f"«{rid}»: option «{option}» не входит в refs")
        elif isinstance(found[1], dict) and found[1].get("status") != "active":
            errors.append(f"«{rid}»: option «{option}» не активен")
    if (
        kind == "claim"
        and rec.get("status") == "active"
        and rec.get("type") == "fact"
        and isinstance(refs, list)
        and refs
        and not _fact_supported_map(all_records, refs)
    ):
        errors.append(
            f"«{rid}»: факт опирается только на синтетические источники"
        )


def _validate_supersedes_state(kind, rid, rec, all_records, errors):
    sup = rec.get("supersedes")
    if sup is None:
        return
    if not isinstance(sup, str) or not RECORD_ID.fullmatch(sup):
        errors.append(f"«{rid}»: supersedes не похож на ID")
        return
    found = all_records.get(sup)
    if not found:
        errors.append(f"«{rid}»: supersedes «{sup}» не найден")
        return
    sup_kind, sup_rec = found
    if sup_kind != kind:
        errors.append(f"«{rid}»: supersedes указывает на {sup_kind}")
    elif isinstance(sup_rec, dict) and sup_rec.get("status") != "superseded":
        errors.append(f"«{rid}»: «{sup}» не имеет статус superseded")


def _validate_costs(costs, errors):
    for index, item in enumerate(costs):
        label = f"costs[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: запись не объект")
            continue
        if item.get("kind") not in COST_KINDS:
            errors.append(f"{label}: kind должен быть model/search/tool/human")
        for field in ("usd", "minutes"):
            value = item.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                errors.append(f"{label}: {field} должен быть строкой или null")
                continue
            try:
                _decimal(value, f"{label} {field}")
            except ValueError as exc:
                errors.append(str(exc))
        note = item.get("note")
        if not isinstance(note, str) or not note.strip():
            errors.append(f"{label}: нужен непустой note")


def _validate_events(events, errors):
    seen_keys = set()
    for index, event in enumerate(events):
        label = f"events[{index}]"
        if not isinstance(event, dict):
            errors.append(f"{label}: событие не объект")
            continue
        key = event.get("key")
        if not isinstance(key, str) or not RECORD_ID.fullmatch(key):
            errors.append(f"{label}: key не похож на ID")
        elif key in seen_keys:
            errors.append(f"{label}: повтор ключа «{key}»")
        else:
            seen_keys.add(key)
        if event.get("action") not in ACTIONS:
            errors.append(f"{label}: неизвестное действие «{event.get('action')}»")
        if not isinstance(event.get("payload"), dict):
            errors.append(f"{label}: payload должен быть объектом")
        at = event.get("at")
        if not isinstance(at, str):
            errors.append(f"{label}: at должен быть строкой")
        else:
            try:
                datetime.fromisoformat(at)
            except ValueError:
                errors.append(f"{label}: at не ISO-время «{at}»")
        revision = event.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            errors.append(f"{label}: revision должен быть целым")
        elif revision != index + 1:
            errors.append(f"{label}: revision {revision} != {index + 1}")


def _validate_materials(project, all_records, errors):
    mdir = _materials_dir(project)
    if os.path.islink(mdir):
        errors.append("materials — symlink")
        return
    root = Path(os.path.realpath(project))
    for rid, (kind, rec) in all_records.items():
        if kind != "source" or not isinstance(rec, dict):
            continue
        path = rec.get("path")
        if not isinstance(path, str) or not path:
            continue
        parts = path.split("/")
        candidate = project.joinpath(*parts)
        real = Path(os.path.realpath(candidate))
        if real != root and root not in real.parents:
            errors.append(f"«{rid}»: файл материала вне проекта")
            continue
        if os.path.islink(candidate):
            errors.append(f"«{rid}»: материал — symlink")
            continue
        if not candidate.is_file():
            errors.append(f"«{rid}»: файл материала не найден: {path}")
            continue
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"«{rid}»: не читается материал: {exc}")
            continue
        if digest != rec.get("sha256"):
            errors.append(f"«{rid}»: хеш материала не совпадает с записью")


def validate_state(state, project=None):
    """Структурная проверка. Возвращает (errors, warnings)."""
    errors = []
    warnings = []
    if not isinstance(state, dict):
        return ["state.json должен содержать объект"], []
    if state.get("version") != 1:
        errors.append("version должен быть 1")
    for field in ("goal", "owner", "stage", "next", "budget_usd"):
        if not isinstance(state.get(field), str):
            errors.append(f"Поле «{field}» должно быть строкой")
    if state.get("stage") not in STAGES:
        errors.append(f"Этап «{state.get('stage')}» не из списка {list(STAGES)}")
    if not isinstance(state.get("paused"), bool):
        errors.append("Поле paused должно быть true или false")
    hosts = state.get("allow_hosts")
    if not isinstance(hosts, list) or any(not isinstance(h, str) for h in hosts):
        errors.append("allow_hosts должен быть списком строк")
        hosts = []
    else:
        seen_hosts = set()
        for host in hosts:
            try:
                _clean_host(host)
            except ValueError as exc:
                errors.append(str(exc))
            if host in seen_hosts:
                warnings.append(f"allow_hosts: повтор «{host}»")
            seen_hosts.add(host)
    try:
        _decimal(state.get("budget_usd"), "budget_usd")
    except ValueError as exc:
        errors.append(str(exc))

    collections = {}
    for name in ("sources", "claims", "options", "decisions"):
        value = state.get(name)
        if not isinstance(value, dict):
            errors.append(f"«{name}» должен быть объектом ID→запись")
            value = {}
        collections[name] = value
    costs = state.get("costs")
    if not isinstance(costs, list):
        errors.append("«costs» должен быть списком")
        costs = []
    events = state.get("events")
    if not isinstance(events, list):
        errors.append("«events» должен быть списком")
        events = []

    revision = state.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        errors.append("revision должен быть целым числом ≥ 0")
    elif revision != len(events):
        errors.append(
            f"revision ({revision}) не совпадает с числом событий ({len(events)})"
        )

    all_records = {}
    for name, coll in collections.items():
        kind = COLLECTION_KIND[name]
        for rid, rec in coll.items():
            if not isinstance(rid, str) or not RECORD_ID.fullmatch(rid):
                errors.append(f"{name}: некорректный ID «{rid}»")
                continue
            if rid in all_records:
                errors.append(f"ID «{rid}» встречается более одного раза")
            all_records[rid] = (kind, rec)
            if not isinstance(rec, dict):
                errors.append(f"«{rid}»: запись не объект")
                continue
            if rec.get("id") != rid:
                errors.append(
                    f"«{rid}»: поле id («{rec.get('id')}») не совпадает с ключом"
                )
            status = rec.get("status")
            if status not in STATUS_BY_KIND[kind]:
                errors.append(
                    f"«{rid}»: недопустимый статус «{status}» для типа {kind}"
                )
            _record_field_errors(kind, rid, rec, state, errors)

    for rid, (kind, rec) in all_records.items():
        if not isinstance(rec, dict):
            continue
        _validate_refs_state(kind, rid, rec, all_records, errors)
        _validate_supersedes_state(kind, rid, rec, all_records, errors)

    supersede_claims = {}
    for rid, (kind, rec) in all_records.items():
        if isinstance(rec, dict) and isinstance(rec.get("supersedes"), str):
            supersede_claims.setdefault(rec["supersedes"], []).append(rid)
    for target, claimers in supersede_claims.items():
        if len(claimers) > 1:
            errors.append(
                f"«{target}»: несколько записей ссылаются supersedes: "
                + ", ".join(sorted(claimers))
            )

    adjacency = {rid: [] for rid in all_records}
    for rid, (kind, rec) in all_records.items():
        if not isinstance(rec, dict) or kind == "source":
            continue
        for ref in rec.get("refs") or []:
            if isinstance(ref, str) and ref in all_records:
                adjacency[rid].append(ref)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {rid: WHITE for rid in all_records}

    def visit(node):
        color[node] = GRAY
        for nxt in adjacency.get(node, []):
            if color.get(nxt) == GRAY:
                errors.append(f"Цикл ссылок через «{node}» и «{nxt}»")
                return
            if color.get(nxt) == WHITE:
                visit(nxt)
        color[node] = BLACK

    sys.setrecursionlimit(max(sys.getrecursionlimit(), len(all_records) + 100))
    for rid in all_records:
        if color[rid] == WHITE:
            visit(rid)

    _validate_costs(costs, errors)
    _validate_events(events, errors)

    for rid, (kind, rec) in all_records.items():
        if isinstance(rec, dict) and rec.get("status") in ATTENTION_STATUSES:
            warnings.append(f"«{rid}» имеет статус {rec.get('status')} — требует проверки")

    try:
        summary = summarize_costs(costs)
        if summary["unknown_entries"]:
            warnings.append(
                f"{summary['unknown_entries']} записей стоимости без цены — "
                "полная сумма неизвестна"
            )
    except Exception:
        pass

    if project is not None:
        _validate_materials(project, all_records, errors)
    return errors, warnings


def cmd_check(args):
    project = _resolve_existing_project(args.project)
    state = _load_state(project)
    errors, warnings = validate_state(state, project)
    info = _budget_info(state)
    if info["budget"] and info["budget"].get("exceeded"):
        errors.append("Известные расходы превышают budget_usd проекта")
    elif info["summary"].get("error"):
        errors.append(f"Не удалось проверить расходы: {info['summary']['error']}")
    stage = state.get("stage")
    if stage in STAGES and STAGES.index(stage) >= STAGES.index("strategy"):
        ok, reason = _option_gate(state)
        if not ok:
            warnings.append(
                f"Этап {stage}, но условия входа в strategy сейчас не "
                f"выполнены: {reason}"
            )
    out = {
        "ok": not errors, "valid": not errors, "errors": errors,
        "warnings": warnings, "stage": stage,
        "budget_usd": state.get("budget_usd"),
        "note": "Проверяется структура записей, а не правильность стратегии",
    }
    return out, 0 if not errors else 1


def _search_text(kind, rec):
    if kind == "source":
        fields = ("id", "kind", "url", "locator", "path")
        return " ".join(str(rec.get(f) or "") for f in fields)
    if kind == "claim":
        fields = ("id", "text", "locator")
    elif kind == "option":
        fields = ("id", "text", "approach")
    else:
        fields = ("id", "text", "option", "metric", "trigger")
    parts = [str(rec.get(f) or "") for f in fields]
    if kind == "option" and isinstance(rec.get("objections"), list):
        parts.extend(str(x) for x in rec["objections"])
    return " ".join(parts)


def _snippet(text, tokens):
    folded = text.casefold()
    positions = [folded.find(t) for t in tokens]
    positions = [p for p in positions if p >= 0]
    index = min(positions) if positions else 0
    start = max(0, index - 50)
    end = min(len(text), index + 90)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return " ".join(snippet.split())


def cmd_find(args):
    project = _resolve_existing_project(args.project)
    state = _load_state(project)
    query = (args.query or "").strip()
    if not query:
        raise ValueError("Нужен --query")
    tokens = [t for t in re.split(r"\s+", query.casefold()) if t]
    if not tokens:
        raise ValueError("Пустой запрос")
    matches = []
    for name in ("sources", "claims", "options", "decisions"):
        kind = COLLECTION_KIND[name]
        for rid, rec in (state.get(name) or {}).items():
            if not isinstance(rec, dict):
                continue
            text = _search_text(kind, rec)
            folded = text.casefold()
            if all(token in folded for token in tokens):
                matches.append({
                    "id": rid, "kind": kind,
                    "status": rec.get("status"),
                    "snippet": _snippet(text, tokens),
                    "refs": rec.get("refs") or [],
                })
    order = {kind: i for i, kind in enumerate(COLLECTIONS)}
    matches.sort(key=lambda m: (order[m["kind"]], m["id"]))
    return {
        "ok": True, "query": query, "count": len(matches),
        "matches": matches,
    }, 0


def cmd_impact(args):
    project = _resolve_existing_project(args.project)
    state = _load_state(project)
    rid = args.id
    found = _lookup(state, rid)
    if not found:
        raise ValueError(f"Запись «{rid}» не найдена")
    kind, rec = found
    dependents = {}
    for name in ("claims", "options", "decisions"):
        for other_id, other in (state.get(name) or {}).items():
            if not isinstance(other, dict):
                continue
            for ref in other.get("refs") or []:
                if isinstance(ref, str):
                    dependents.setdefault(ref, []).append(other_id)
    parent = {rid: None}
    queue = deque([rid])
    visited = {rid}
    while queue:
        node = queue.popleft()
        for dep in dependents.get(node, []):
            if dep in visited:
                continue
            visited.add(dep)
            parent[dep] = node
            queue.append(dep)
    affected = []
    for dep in sorted(visited - {rid}):
        path = []
        node = dep
        while node is not None:
            path.append(node)
            node = parent[node]
        path.reverse()
        dep_found = _lookup(state, dep)
        dep_kind, dep_rec = dep_found if dep_found else ("?", {})
        affected.append({
            "id": dep, "kind": dep_kind,
            "status": dep_rec.get("status") if isinstance(dep_rec, dict) else None,
            "paths": [path],
        })
    return {
        "ok": True, "id": rid, "kind": kind,
        "status": rec.get("status") if isinstance(rec, dict) else None,
        "dependents": affected,
    }, 0


def cmd_review(args):
    project = _resolve_existing_project(args.project)
    state = _load_state(project)
    today = _today()
    decisions = []
    for rid, rec in (state.get("decisions") or {}).items():
        if not isinstance(rec, dict):
            continue
        due = rec.get("due") if isinstance(rec.get("due"), str) else ""
        decisions.append({
            "id": rid, "text": rec.get("text"), "option": rec.get("option"),
            "owner": rec.get("owner"), "due": due,
            "past_due": bool(due and due < today),
            "approval": rec.get("approval"), "status": rec.get("status"),
            "metric": rec.get("metric"), "trigger": rec.get("trigger"),
        })
    decisions.sort(key=lambda d: (d["due"] or "9999", d["id"]))
    pending = [
        d["id"] for d in decisions
        if d["approval"] == "pending" and d["status"] == "active"
    ]
    stale, review_required = [], []
    for name in ("claims", "options", "decisions"):
        for rid, rec in (state.get(name) or {}).items():
            if not isinstance(rec, dict):
                continue
            if rec.get("status") == "stale":
                stale.append(rid)
            elif rec.get("status") == "review_required":
                review_required.append(rid)
    out = {
        "ok": True, "today": today, "decisions": decisions,
        "pending_approvals": sorted(pending),
        "needs_review": sorted(stale + review_required),
        "stale": sorted(stale), "review_required": sorted(review_required),
        "note": "Триггеры показаны как данные — срабатывание автоматически не проверяется",
    }
    return out, 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Состояние проекта: источники, утверждения, варианты, решения"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Создать состояние проекта")
    init.add_argument("project")
    init.add_argument("--goal", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--allow", action="append", default=[], metavar="HOST")
    init.add_argument("--budget-usd", default="0")

    source = commands.add_parser("source", help="Зарегистрировать источник")
    source.add_argument("project")
    source.add_argument("--id", required=True)
    source.add_argument("--file", required=True)
    source.add_argument("--kind", required=True, choices=sorted(SOURCE_KINDS))
    source.add_argument("--url")
    source.add_argument("--locator", required=True)
    source.add_argument("--key", required=True)
    source.add_argument("--supersedes")

    record = commands.add_parser("record", help="Записать claim/option/decision")
    record.add_argument("project")
    record.add_argument("--kind", required=True, choices=["claim", "option", "decision"])
    record.add_argument("--json", required=True)
    record.add_argument("--key", required=True)

    checkpoint = commands.add_parser("checkpoint", help="Сменить этап и следующий шаг")
    checkpoint.add_argument("project")
    checkpoint.add_argument("--stage", required=True, choices=list(STAGES))
    checkpoint.add_argument("--next", required=True)
    checkpoint.add_argument("--key", required=True)

    pause = commands.add_parser("pause", help="Приостановить изменения")
    pause.add_argument("project")
    pause.add_argument("--next", required=True)
    pause.add_argument("--key", required=True)

    resume = commands.add_parser("resume", help="Снять паузу")
    resume.add_argument("project")
    resume.add_argument("--key", required=True)

    for name, helptext in (
        ("status", "Показать состояние"),
        ("check", "Проверить структуру state"),
        ("review", "Повестка: сроки, согласования, пересмотр"),
    ):
        sub = commands.add_parser(name, help=helptext)
        sub.add_argument("project")

    find = commands.add_parser("find", help="Локальный поиск по записям")
    find.add_argument("project")
    find.add_argument("--query", required=True)

    impact = commands.add_parser("impact", help="Зависимые записи от ID")
    impact.add_argument("project")
    impact.add_argument("id")

    retract = commands.add_parser("retract", help="Отозвать запись")
    retract.add_argument("project")
    retract.add_argument("id")
    retract.add_argument("--reason", required=True)
    retract.add_argument("--key", required=True)

    cost = commands.add_parser("cost", help="Записать расход")
    cost.add_argument("project")
    cost.add_argument("--kind", required=True, choices=sorted(COST_KINDS))
    cost.add_argument("--usd")
    cost.add_argument("--minutes")
    cost.add_argument("--note", required=True)
    cost.add_argument("--key", required=True)

    return parser


COMMANDS = {
    "init": cmd_init,
    "source": cmd_source,
    "record": cmd_record,
    "checkpoint": cmd_checkpoint,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "status": cmd_status,
    "check": cmd_check,
    "find": cmd_find,
    "impact": cmd_impact,
    "retract": cmd_retract,
    "review": cmd_review,
    "cost": cmd_cost,
}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, code = COMMANDS[args.command](args)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
