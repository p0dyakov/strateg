"""Replay an explicitly educational decision record without network or LLM calls."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(project, action, *args):
    command = [sys.executable, str(ROOT / "scripts/work.py"), action, str(project), *args]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    data = json.loads(result.stdout)
    print(json.dumps({"action": action, "result": data}, ensure_ascii=False))
    return data


def record(project, kind, key, **fields):
    return run(
        project, "record", "--kind", kind,
        "--json", json.dumps(fields, ensure_ascii=False), "--key", key,
    )


def seed(project, update=False):
    project = Path(project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    for name in [
        "brief.md", "data.csv", "strategy.md",
        "inbox/product.md", "inbox/research.md", "inbox/factory.md",
    ]:
        source, target = ROOT / "demo" / name, project / name
        if source == target:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError(f"Не перезаписываю изменённый файл {target}")
        if not target.exists():
            target.write_bytes(source.read_bytes())
    if not (project / "state.json").exists():
        run(
            project, "init", "--goal", "Выбрать следующий шаг для сценария Евы во время встреч",
            "--owner", "Руководитель продукта, учебная роль",
            "--allow", "moscow.megafon.ru", "--allow", "trends.rbc.ru",
            "--budget-usd", "0",
        )
    run(project, "checkpoint", "--stage", "research", "--next", "Проверить описание продукта", "--key", "d-start")
    sources = [
        ("S1", "inbox/product.md", "open", "https://moscow.megafon.ru/services/security/eva/", "Работаю секретарём"),
        ("S2", "inbox/research.md", "open", "https://moscow.megafon.ru/help/info/popular/ux_research/", "Как проходят исследования"),
        ("S3", "inbox/factory.md", "open", "https://trends.rbc.ru/trends/innovation/cmrm/6123a67d9a79476d474f2d38", "Что на практике"),
        ("S4", "data.csv", "synthetic", "", "Строки 1-5, полностью учебные агрегаты"),
    ]
    for sid, file, kind, url, locator in sources:
        args = ["--id", sid, "--file", file, "--kind", kind, "--locator", locator, "--key", f"d-{sid}"]
        if url:
            args.extend(["--url", url])
        run(project, "source", *args)
    claims = [
        {"id": "C1", "type": "fact", "text": "Сайт Евы описывает приём звонка, когда человек занят", "refs": ["S1"], "locator": "Работаю секретарём"},
        {"id": "C2", "type": "fact", "text": "МегаФон приглашает участников в UX-исследования", "refs": ["S2"], "locator": "Как проходят исследования"},
        {"id": "C3", "type": "fact", "text": "В 2021 году Соболев описывал проверку прототипов с клиентами", "refs": ["S3"], "locator": "Что на практике"},
        {"id": "H1", "type": "hypothesis", "text": "Возможен барьер понимания пользы секретаря во время встреч", "refs": ["C1"]},
        {"id": "U1", "type": "unknown", "text": "Неизвестны спрос, затраты, качество и фактическое использование сценария", "refs": []},
        {"id": "H2", "type": "hypothesis", "text": "Учебный пример иллюстрирует риск изменения состава когорт, не причину удержания Евы", "refs": ["S4"]},
    ]
    for item in claims:
        record(project, "claim", f"d-{item['id']}", **item)
    run(project, "checkpoint", "--stage", "options", "--next", "Сравнить разные пути", "--key", "d-options")
    options = [
        ("O1", "status_quo", "Оставить как есть и наблюдать", ["C1", "U1"], "Можно упустить реальный барьер"),
        ("O2", "communication", "Проверить объяснение существующего сценария", ["C1", "H1", "U1"], "Если мешает качество или цена, текст не поможет"),
        ("O3", "product", "Развивать отдельный режим для встреч", ["C1", "H1", "U1"], "Не доказана недостаточность существующих настроек"),
        ("O4", "close", "Закрыть новое направление встреч, не Еву целиком", ["C1", "U1"], "Можно отказаться от полезного сценария до проверки"),
    ]
    for oid, approach, text, refs, objection in options:
        record(project, "option", f"d-{oid}", id=oid, approach=approach, text=text, refs=refs, objections=[objection])
    run(project, "checkpoint", "--stage", "strategy", "--next", "Проверить согласованность двух стратегий", "--key", "d-strategy")
    run(project, "pause", "--next", "Сохранён проект стратегии, продолжить с учебной записью решения", "--key", "d-pause")
    run(project, "status")
    run(project, "resume", "--key", "d-resume")
    run(project, "checkpoint", "--stage", "decision", "--next", "Нужен выбор человека, в демо только учебный пример", "--key", "d-choice")
    record(
        project, "decision", "d-D1", id="D1",
        text="Учебный выбор O2, не реальное согласование",
        refs=["O2", "C1", "H1", "U1"], option="O2",
        owner="Руководитель продукта, учебная роль", due="2026-12-18",
        metric="Понимание условий, успешное использование, жалобы и цена проверки",
        trigger="Проблема не подтверждена, условия изменились или проверка слишком дорога",
        approval="example",
    )
    run(project, "cost", "--kind", "tool", "--usd", "0", "--note", "Локальный скрипт без платных вызовов", "--key", "d-cost-tool")
    run(project, "cost", "--kind", "model", "--note", "Цена подготовки стратегии в хосте не раскрыта, это не нулевой расход", "--key", "d-cost-model")
    run(project, "checkpoint", "--stage", "followup", "--next", "Вернуться к учебному решению 2026-12-18 или при новых данных", "--key", "d-followup")
    if update:
        run(project, "retract", "H1", "--reason", "Учебный сценарий пересмотра: допущение отозвано для новой проверки, реальных интервью не было", "--key", "d-update")
        run(project, "impact", "H1")
    run(project, "check")
    run(project, "review")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Учебное демо инструментов, не автономный запуск LLM")
    parser.add_argument("--project", default=str(ROOT / "demo"))
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    try:
        seed(args.project, args.update)
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
