"""Optional recorded checks with a real host model, never an autonomous service."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUEST = """Нужно решить, стоит ли развивать сценарий Евы для людей,
которые не могут отвечать во время встреч, и как объяснять пользу
Дай картину ситуации, минимум три разных варианта со статус-кво и закрытием
нового направления, сильные возражения, продуктовую и коммуникационную
стратегии, план проверки и условия пересмотра
Не выдавай учебные данные за данные МегаФона
Реальных интервью нет, ничего не запускай и никому не пиши
"""
FIRST = """Прочитай AGENTS.md и нужные навыки
Рабочий проект только runs/live, исходные материалы уже лежат в нём
Прочитай runs/live/brief.md, runs/live/inbox/product.md и runs/live/data.csv
Сети и MCP нет, используй только эти материалы и локальные инструменты
Не читай другие проекты, другие runs и ответы baseline
Работай кратко, не меняй код, правила, навыки и материалы
Записывать можно только в runs/live
Можно использовать Bash только для python3 scripts/work.py и python3 scripts/data.py
Сначала создай state через init с --goal и --owner по брифу,
укажи --allow moscow.megafon.ru --budget-usd 0
Зарегистрируй S1 с описанием продукта, C1 как факт о заявлении сайта,
H1 как гипотезу о непонимании пользы и U1 как пробел о спросе и экономике
Сохрани не меньше трёх вариантов через record, включая status_quo и close
Не утверждай, что H1 подтверждена
Напиши runs/live/strategy.md с разделами Продуктовая стратегия,
Коммуникационная стратегия и План проверки
Покажи ограничения расчёта, если используешь учебную таблицу
До выбора человека дойди только до checkpoint strategy
Затем выполни pause с конкретным --next и остановись
Решение не согласовано, не записывай approved или example на этом шаге
В конце дай короткий итог, расходы неизвестны, не считай их нулевыми
"""
NEXT = """Это новый сеанс без истории
Прочитай AGENTS.md, навык resume и состояние проекта runs/live
Продолжи сохранённую работу, не создавай новый state и не повторяй исследование
Для учебной проверки выбери предложенный пилот объяснения существующей функции
Это не реальное согласование, используй только approval example, не approved
Зафиксируй D1 с владельцем-ролью, сроком 2026-12-18, показателем,
условием пересмотра и ссылками на вариант и H1, перейди в followup
Затем прочитай runs/live/inbox/update.md и навык update
Это учебная поправка, а не новые реальные интервью
Зарегистрируй её как synthetic, отзови H1 через retract с причиной
Выполни impact H1, review и check, покажи что затронуло D1
Добавь в strategy.md короткую пометку о пересмотре, не стирай прежний вывод
Код, правила и исходные материалы не меняй
Сети нет, записывать можно только runs/live
В конце назови следующий выбор человека и ограничения свидетельств
"""


def complete(project, mode):
    errors = []
    try:
        state = json.loads((project / "state.json").read_text(encoding="utf-8"))
        strategy = (project / "strategy.md").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return [f"Не сохранены обязательные артефакты: {exc}"]
    for title in ("Продуктовая стратегия", "Коммуникационная стратегия", "План проверки"):
        if title.casefold() not in strategy.casefold():
            errors.append(f"Нет раздела: {title}")
    if mode == "first":
        if state.get("stage") != "strategy" or not state.get("paused"):
            errors.append("Не сохранена пауза на этапе strategy")
        if not state.get("sources") or not {"C1", "H1", "U1"} <= state.get("claims", {}).keys():
            errors.append("Не сохранены источники и обязательные утверждения")
        options = state.get("options", {})
        approaches = {item.get("approach") for item in options.values()}
        if len(options) < 3 or not {"status_quo", "close"} <= approaches:
            errors.append("Нет трёх вариантов со статус-кво и закрытием")
    if mode == "next":
        if state.get("stage") != "followup" or state.get("paused"):
            errors.append("Не продолжен этап followup")
        if state.get("claims", {}).get("H1", {}).get("status") != "retracted":
            errors.append("H1 не отозвана")
        decision = state.get("decisions", {}).get("D1", {})
        if decision.get("status") != "review_required" or decision.get("approval") != "example":
            errors.append("D1 не помечено для учебного пересмотра")
    return errors


def execute(mode, seconds, budget, host, run_id):
    if not shutil.which(host):
        raise ValueError(f"Нужен установленный {host} с вашим доступом")
    destination = ROOT / "runs"
    destination.mkdir(exist_ok=True)
    prefix = f"{host}-{mode}" + (f"-{run_id}" if run_id != "live" else "")
    log = destination / f"{prefix}.jsonl"
    if log.exists():
        raise ValueError(f"Лог уже существует, не повторяю платное действие: {log}")
    if mode == "first":
        project = destination / run_id
        if project.exists():
            raise ValueError(f"{project} уже существует, проверьте сохранённую работу")
        for name in ["brief.md", "inbox/product.md", "data.csv"]:
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "demo" / name, target)
        prompt = REQUEST + FIRST
    elif mode == "next":
        project = destination / run_id
        if not (project / "state.json").exists():
            raise ValueError("Сначала нужен сохранённый первый сеанс")
        previous = complete(project, "first")
        if previous:
            raise ValueError("Первый сеанс не завершён: " + " | ".join(previous))
        update = project / "inbox/update.md"
        update.write_text(
            "# Учебная поправка\n\n"
            "Синтетический сценарий, реальных интервью не было\n"
            "Допущение H1 о непонимании пользы нужно отозвать для новой проверки\n"
            "Причина отказа может быть иной, например качество или условия\n"
            "Это не доказательство другой причины и не рыночное исследование\n",
            encoding="utf-8",
        )
        prompt = NEXT
    else:
        names = ["brief.md", "inbox/product.md", "data.csv"]
        context = "\n\n".join(
            f"Материал {name}\n{(ROOT / 'demo' / name).read_text(encoding='utf-8')}"
            for name in names
        )
        prompt = context + "\n\n" + REQUEST + "\nОтветь только по этим материалам, без инструментов"
    prompt = prompt.replace("runs/live", f"runs/{run_id}")
    if host == "codex" and mode != "baseline":
        prompt = prompt.replace(
            "Можно использовать Bash только для python3 scripts/work.py и python3 scripts/data.py",
            "Для чтения разрешены локальные cat, sed, rg и python3 scripts/read.py\n"
            "Для состояния и расчётов используй scripts/work.py и scripts/data.py\n"
            "Для записи текста разрешён apply_patch, но только внутри папки рабочего проекта",
        )
        prompt += "\nВ этом хосте чтение файлов доступно через локальный терминал, запись текста через apply_patch"
    baseline_dir = tempfile.TemporaryDirectory(prefix="strateg-baseline-") if mode == "baseline" else None
    cwd = Path(baseline_dir.name) if baseline_dir else ROOT
    if host == "codex":
        command = [
            "codex", "-a", "never", "exec", "--ignore-user-config",
            "--skip-git-repo-check", "--ephemeral", "--json",
            "--sandbox", "read-only" if mode == "baseline" else "workspace-write",
            "-c", "sandbox_workspace_write.network_access=false",
            "-C", str(cwd),
        ]
    else:
        command = [
            "claude", "-p", "--model", "sonnet", "--safe-mode", "--restricted",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--output-format", "stream-json", "--verbose",
            "--max-budget-usd", str(budget), "--no-session-persistence",
        ]
        if mode == "baseline":
            command += ["--tools", ""]
        else:
            command += [
                "--append-system-prompt", (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
                "--tools", "Read,Write,Bash",
                "--allowedTools", "Read", "Write(./runs/**)",
                "Bash(python3 scripts/work.py *)",
                "Bash(python3 scripts/data.py *)",
            ]
    command += ["--", prompt]
    started = time.perf_counter()
    stderr = destination / f"{prefix}.stderr"
    timed_out = False
    with log.open("x", encoding="utf-8") as output, stderr.open("x", encoding="utf-8") as errors:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=output, stderr=errors,
            start_new_session=os.name != "nt",
        )
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
    elapsed = time.perf_counter() - started
    if baseline_dir:
        baseline_dir.cleanup()
    result = None
    models = []
    usage = None
    errors_seen = []
    baseline_tools = []
    for line in log.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "result":
            result = event
        if event.get("type") == "turn.completed":
            result = event
            usage = event.get("usage")
        if event.get("type") in ("error", "turn.failed"):
            errors_seen.append(event)
        item = event.get("item", {})
        if mode == "baseline" and item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
            baseline_tools.append(item.get("type"))
        model = event.get("message", {}).get("model")
        if model and model != "<synthetic>" and model not in models:
            models.append(model)
    completion_errors = complete(project, mode) if mode != "baseline" else (
        ["Baseline использовал инструменты"] if baseline_tools else []
    )
    summary = {
        "host": host, "mode": mode, "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode, "timed_out": timed_out,
        "models": models, "has_result": result is not None,
        "reported_usd": result.get("total_cost_usd") if result else None,
        "usage": usage if host == "codex" else result.get("usage") if result else None,
        "is_error": bool(errors_seen) or bool(result and result.get("is_error")),
        "baseline_tools": sorted(set(baseline_tools)),
        "completion_errors": completion_errors,
        "task_complete": not completion_errors and result is not None,
        "cost_note": "Цена из отчёта CLI, не подтверждённое списание с подписки",
        "log": str(log.relative_to(ROOT)),
    }
    (destination / f"{prefix}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if timed_out or process.returncode or not result or summary["is_error"] or completion_errors else 0


def main():
    parser = argparse.ArgumentParser(description="Платная или подписочная проверка реальной LLM")
    parser.add_argument("mode", choices=["baseline", "first", "next"])
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument("--host", choices=["claude", "codex"], default="codex")
    parser.add_argument("--run", default="live", help="Имя нового проверочного проекта и логов")
    parser.add_argument("--budget-usd", type=float)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("Время от 1 до 600 секунд")
    if args.host == "codex" and args.budget_usd is not None:
        parser.error("Codex CLI не даёт денежного лимита, доступен только --seconds")
    if args.budget_usd is not None and not 0 < args.budget_usd <= 5:
        parser.error("Лимит от 0 до 5 USD")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", args.run):
        parser.error("Имя прогона: латинские буквы, цифры и дефис, до 32 символов")
    try:
        return execute(args.mode, args.seconds, args.budget_usd or 2, args.host, args.run)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
