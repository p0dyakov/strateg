# Источники

Прочитано 18 ноября 2026 года
Методы адаптированы к задаче, это не утверждение о внутренних стандартах МегаФона
Сроки, ID, формат состояния и правила блокировок являются решениями этого проекта

| Источник | Что взято | Ограничение |
| --- | --- | --- |
| [МегаФон, Ева](https://moscow.megafon.ru/services/security/eva/) | Описание секретаря и условия функций | Заявления поставщика, московская страница, не метрики качества |
| [МегаФон, UX-лаборатория](https://moscow.megafon.ru/help/info/popular/ux_research/) | Наличие пользовательских исследований | Не результаты исследования нашего сценария |
| [РБК, Александр Соболев, 31.08.2021](https://trends.rbc.ru/trends/innovation/cmrm/6123a67d9a79476d474f2d38) | Публичное описание фабрики и прототипов | Историческое интервью, не текущий регламент |
| [GOV.UK, Discovery](https://www.gov.uk/service-manual/agile-delivery/how-the-discovery-phase-works) | Проблема до решения, границы и допустимость остановки | Метод государственного сервиса адаптирован к продукту |
| [Product Talk, Opportunity Solution Trees, 06.12.2023](https://www.producttalk.org/opportunity-solution-trees/) | Результат, потребности, варианты и проверки | Нельзя придумывать потребности без входных данных |
| [Product Talk, Five Types of Assumptions, 11.10.2023](https://www.producttalk.org/five-types-of-assumptions/) | Спрос, экономика, реализуемость, удобство и этика | Перечень вопросов, не доказательство наших гипотез |
| [Government Communications, OASIS](https://www.communications.gov.uk/publication/oasis-planning-government-communications/) | Цель, аудитория, подход, исполнение и оценка | Источник для коммуникаций, не внутренний стандарт оператора |
| [Microsoft Research, SRM, 14.09.2020](https://www.microsoft.com/en-us/research/articles/diagnosing-sample-ratio-mismatch-in-a-b-testing/) | Проверка распределения до интерпретации A/B | Порог конкретной платформы не универсален |
| [Microsoft Azure, ADR](https://learn.microsoft.com/en-us/azure/well-architected/architecture-decision-record) | Контекст, альтернативы, последствия и замена без удаления | Управление обязательствами добавлено в проекте |
| [NN/g, Synthetic Users](https://www.nngroup.com/articles/synthetic-users/) | Синтетика для гипотез, не замена реальным людям | Не общий тест всех нынешних и будущих моделей |
| [Anthropic, Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) | Простые инструменты, маршрутизация и проверка | Общие принципы, не руководство по актуальному SDK |
| [OWASP, Prompt Injection](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html) | Недоверенные материалы и проверка границ инструментов | Одни инструкции не образуют песочницу |
| [OWASP, MCP Security](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html) | Минимальные права и проверка схем и параметров | Защита зависит от хоста и сервера |
| [Cursor, Skills](https://cursor.com/docs/skills) и [Rules](https://cursor.com/docs/rules) | Пути навыков и AGENTS.md | Аналогичная загрузка в других хостах проверяется отдельно |
| [Pele API](https://pele.run/api.md) | `render` и экспорт SVG | Синтаксис Mermaid, рендерер именно Pele |
| [Claude Code CLI](https://code.claude.com/docs/en/cli-reference) | Неинтерактивная проверка и ограничение инструментов | Один из двух поддержанных хостов live.py, не обязательная среда пользователя |

Исследование использовало Exa, TinyFish Search и Fetch, затем [углублённый отчёт TinyFish с 63 ссылками](https://agent.tinyfish.ai/research/01a1eeef-093e-7d13-8c63-28768df444d5)
Число ссылок отчёта не равно числу независимо проверенных фактов
Его выводы не принимались автоматически
Например, в нём отсутствовал найденный отдельно материал РБК, а коммуникационный метод потребовал отдельного первоисточника OASIS
Цены, статистика и рекомендации из непроверенных пересказов не переносились в стратегию
