# Task6 source discovery notes

Corpus as-of: 2026-08-27. Final registry is China-priority: 10 Chinese public interview pages (牛客), 5 Tencent Cloud official documents, and 5 Baidu current JDs. All summaries and locators are Chinese and concise; no page body, PII, credentials, or gated content is stored.

## Accepted: 20 (10 interview + 5 official + 5 JD)

Each interview entry is a firsthand or explicitly secondary Chinese-language experience with visible publication date and structured `date_evidence_kind=visible_published_date`; official entries use `official_last_updated`; JD entries use `accessed_current_jd` and do not pretend to have a publication date. All are directly accessible and remain `draft` with empty `question_ids` for Task7.

Interview dates: 10/10 fall in 2025-08-27..2026-08-27 and 10/10 fall in 2026-02-28..2026-08-27. Domestic share is 10/10 interviews and 10/10 official/JD. Dimensions collectively cover role_dim_01..role_dim_06. Each source has a non-empty date evidence locator and raw date checked against the model date.

## Rejected: 4

| URL | reason |
|---|---|
| https://interviewing.io/ | blocked by access policy |
| https://www.glassdoor.com/ | login/restricted content |
| https://leetcode.com/discuss/post/6466098/Flexport-or-SDE-1-or-Online-Assessment-and-Interview-Experience-or-Feb-2025/ | direct fetch timeout; no inferred date |
| https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview?utm_source=mail | tracking-only duplicate of canonical page |

All prior foreign-source counts are historical and superseded by this China-priority registry. Task7 read-only audit must still fail honestly because no questions or sidecars are populated.
