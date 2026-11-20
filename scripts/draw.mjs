import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { render } from 'pele';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const assetsDir = join(root, 'assets');

const FLOW_SOURCE = `stateDiagram-v2
  [*] --> Brief
  state "Вопрос решения" as Brief
  state "Исследование" as Research
  state "Варианты" as Options
  state "Две стратегии" as Strategy
  state "Выбор человека" as Decision
  state "Сопровождение" as Followup
  Brief --> Research
  Research --> Options
  Options --> Strategy
  Strategy --> Decision
  Decision --> Followup: Согласовано
  Decision --> Research: Нужны основания
  Followup --> Research: Новые данные`;

const MEMORY_SOURCE = `sequenceDiagram
  actor User as Руководитель
  participant Agent as LLM-агент
  participant Tools as Локальные инструменты
  participant Files as Файлы проекта
  User->>Agent: Запрос или продолжение
  Agent->>Files: Читает brief и state
  Agent->>Tools: Выбирает проверку
  Tools->>Files: Сохраняет источник и связи
  Tools-->>Agent: Результат и ошибки
  Agent->>Files: Основания и следующий шаг
  Agent-->>User: Варианты и рекомендация
  User->>Agent: Решение
  Agent->>Files: Обязательства и пересмотр`;

const STACK_SOURCE = `flowchart TB
  subgraph Host["Агентный хост — IDE или CLI"]
    Agent["LLM-агент"]
  end
  subgraph Repo["Репозиторий"]
    Rules["AGENTS.md — правила и безопасность"]
    Skills[".agents/skills — 12 процедур по задаче"]
    Method["knowledge/method.md — метод"]
    Tools["scripts/*.py — без сети"]
  end
  subgraph Proj["Проект"]
    Inbox["inbox, materials — материалы"]
    State["state.json — источники, связи, решения"]
    Strategy["strategy.md — результат"]
  end
  Agent --> Rules
  Agent --> Skills
  Agent --> Method
  Agent <--> Tools
  Agent <--> Inbox
  Tools <--> State
  Agent --> Strategy`;

const EVIDENCE_SOURCE = `flowchart LR
  Src["Источник<br>open · own · synthetic"]
  Claim["Утверждение<br>fact · hypothesis<br>forecast · unknown"]
  Option["Вариант<br>status_quo · close<br>другой механизм"]
  Decision["Решение<br>owner · due · metric<br>trigger · approval"]
  New["Запись новее"]
  Src -- refs --> Claim
  Claim -- refs --> Option
  Option -- option --> Decision
  Claim -- refs --> Decision
  New -. supersedes .-> Claim
  Claim -. отзыв: stale .-> Decision`;

const OVERVIEW_SOURCE = `flowchart LR
  Req([Запрос руководителя])
  Mats([Материалы · открытые источники])
  Agent[LLM-агент<br>AGENTS.md · навыки · метод]
  Tools[scripts/*.py<br>память · проверки · без сети]
  State[(state.json<br>источники · связи · решения)]
  Res[strategy.md<br>стратегия и план проверки]
  Human{{Выбор человека}}
  Req --> Agent
  Mats --> Agent
  Agent <--> Tools
  Tools <--> State
  Agent --> Res
  Res --> Human
  Human -. новые данные .-> Agent`;

const DIAGRAMS = [
  { file: 'overview.svg', idPrefix: 'overview-', source: OVERVIEW_SOURCE, expect: 'flowchart' },
  { file: 'flow.svg', idPrefix: 'flow-', source: FLOW_SOURCE, expect: 'state' },
  { file: 'stack.svg', idPrefix: 'stack-', source: STACK_SOURCE, expect: 'flowchart' },
  { file: 'evidence.svg', idPrefix: 'evidence-', source: EVIDENCE_SOURCE, expect: 'flowchart' },
  { file: 'memory.svg', idPrefix: 'memory-', source: MEMORY_SOURCE, expect: 'sequence' },
];

function renderDiagram({ idPrefix, source }) {
  const result = render(source, {
    fontFamily: 'Georgia',
    fontSize: 17,
    idPrefix,
    links: false,
    images: false,
  });
  return result;
}

function withWhiteBackground(svg) {
  const background =
    '<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>';
  return svg.replace(/<svg([^>]*)>/, `<svg$1>${background}`);
}

const check = process.argv.includes('--check');
let failed = false;

for (const diagram of DIAGRAMS) {
  const result = renderDiagram(diagram);
  if (!result.type.toLowerCase().includes(diagram.expect)) {
    console.error(`${diagram.file}: неожиданный тип диаграммы ${result.type}`);
    failed = true;
    continue;
  }
  const svg = withWhiteBackground(result.svg);
  const target = join(assetsDir, diagram.file);
  if (check) {
    let existing;
    try {
      existing = readFileSync(target, 'utf8');
    } catch {
      console.error(`${diagram.file}: файл отсутствует — выполните npm run diagrams`);
      failed = true;
      continue;
    }
    if (existing !== svg) {
      console.error(`${diagram.file}: содержимое устарело — выполните npm run diagrams`);
      failed = true;
    }
  } else {
    mkdirSync(assetsDir, { recursive: true });
    writeFileSync(target, svg);
    console.log(`${diagram.file}: записано (${result.type}, ${svg.length} байт)`);
  }
}

process.exit(failed ? 1 : 0);
