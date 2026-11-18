"""Reproducible descriptive rates and explicit cost accounting."""

import argparse
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


def nonnegative(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Нужно неотрицательное конечное число") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Нужно неотрицательное конечное число")
    return result


def summarize_costs(records):
    known = Decimal("0")
    minutes = Decimal("0")
    unknown = 0
    for record in records:
        if record.get("usd") is None:
            unknown += 1
        else:
            known += nonnegative(record["usd"])
        if record.get("minutes") is not None:
            minutes += nonnegative(record["minutes"])
    return {
        "known_usd": str(known),
        "unknown_entries": unknown,
        "total_usd": None if unknown else str(known),
        "recorded_minutes": str(minutes),
    }


def integer(value, field):
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError(f"{field}: нужно целое неотрицательное число")
    return int(text)


def rates(path, base):
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Таблица больше 5 МиБ")
    with io.StringIO(raw.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        columns = [
            "cohort", "segment", "eligible", "retained",
            "window_days", "observed_days",
        ]
        if reader.fieldnames != columns:
            raise ValueError("Нужны столбцы: " + ",".join(columns))
        rows = list(reader)
    if not rows:
        raise ValueError("Таблица пуста")
    seen = set()
    windows = set()
    groups = defaultdict(dict)
    excluded = []
    details = []
    for line, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Строка {line}: неверное число столбцов")
        cohort, segment = row["cohort"].strip(), row["segment"].strip()
        if not cohort or not segment:
            raise ValueError(f"Строка {line}: пустая когорта или сегмент")
        key = cohort, segment
        if key in seen:
            raise ValueError(f"Строка {line}: повтор пары когорта и сегмент")
        seen.add(key)
        n, k, window, observed = (
            integer(row[field], field)
            for field in columns[2:]
        )
        if k > n or window == 0:
            raise ValueError(f"Строка {line}: неверный числитель или окно")
        windows.add(window)
        if observed < window:
            excluded.append({
                "line": line, "cohort": cohort, "segment": segment,
                "reason": "Неполное окно наблюдения",
            })
            continue
        groups[cohort][segment] = (n, k)
        details.append({
            "line": line, "cohort": cohort, "segment": segment,
            "eligible": n, "retained": k, "rate": k / n if n else None,
        })
    if len(windows) != 1:
        raise ValueError("В одной таблице нельзя смешивать окна показателя")
    if base not in groups:
        raise ValueError("Нет зрелых строк базовой когорты")
    base_n = sum(n for n, _ in groups[base].values())
    if not base_n:
        raise ValueError("Нулевой знаменатель базовой когорты")
    weights = {
        segment: n / base_n
        for segment, (n, _) in groups[base].items() if n
    }
    cohorts = {}
    for cohort, segments in groups.items():
        n = sum(pair[0] for pair in segments.values())
        k = sum(pair[1] for pair in segments.values())
        missing = [
            segment for segment in weights
            if segment not in segments or not segments[segment][0]
        ]
        standardized = None if missing else math.fsum(
            weight * segments[segment][1] / segments[segment][0]
            for segment, weight in weights.items()
        )
        cohorts[cohort] = {
            "eligible": n, "retained": k, "rate": k / n if n else None,
            "standardized_rate": standardized,
            "missing_base_segments": missing,
            "extra_segments": sorted(set(segments) - set(weights)),
        }
    warnings = ["Описательное сравнение, не оценка причинного эффекта"]
    if excluded:
        warnings.append("Незрелые строки исключены, проверьте сопоставимость когорт")
    if any(
        item["missing_base_segments"] or item["extra_segments"]
        for item in cohorts.values()
    ):
        warnings.append("Состав сегментов отличается от базовой когорты")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "metric": "retained / eligible",
        "window_days": next(iter(windows)),
        "base": base, "weights": weights, "cohorts": cohorts,
        "rows": details, "excluded": excluded, "warnings": warnings,
    }


def srm(a, b, expected_a, alpha):
    a, b = integer(a, "a"), integer(b, "b")
    expected_a, alpha = float(expected_a), float(alpha)
    if not (
        math.isfinite(expected_a) and 0 < expected_a < 1
        and math.isfinite(alpha) and 0 < alpha < 1
    ):
        raise ValueError("Доля назначения и порог должны быть между 0 и 1")
    total = a + b
    if total == 0:
        raise ValueError("Нет участников")
    ea, eb = total * expected_a, total * (1 - expected_a)
    if min(ea, eb) < 5:
        raise ValueError("Ожидаемая частота меньше 5, приближение не подходит")
    chi2 = (a - ea) ** 2 / ea + (b - eb) ** 2 / eb
    p = math.erfc(math.sqrt(chi2 / 2))
    return {
        "chi2": chi2, "df": 1, "p": p, "alpha": alpha,
        "expected_a": expected_a, "srm_detected": p < alpha,
        "limitation": "SRM не оценивает эффект и не доказывает качество логирования",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Расчёты без вызова модели")
    commands = parser.add_subparsers(dest="command", required=True)
    rate = commands.add_parser("rates", help="Доли и стандартизация состава когорт")
    rate.add_argument("file")
    rate.add_argument("--base", required=True)
    ratio = commands.add_parser("srm", help="Проверка распределения двух групп")
    ratio.add_argument("--a", required=True)
    ratio.add_argument("--b", required=True)
    ratio.add_argument("--expected-a", required=True)
    ratio.add_argument("--alpha", required=True)
    args = parser.parse_args(argv)
    try:
        result = rates(args.file, args.base) if args.command == "rates" else srm(
            args.a, args.b, args.expected_a, args.alpha,
        )
    except (ValueError, OSError, UnicodeError, csv.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
