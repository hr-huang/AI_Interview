# Task6 source discovery notes

Corpus as-of: 2026-08-27. The 20 accepted records were opened as direct pages on 2026-08-27. Summaries are short original descriptions; no page text, personal contact data, credentials, or gated content is stored. Interview `published_at` is the page publication timestamp cue, while any interview timeline is only noted in the locator.

## Accepted sources (20: 10 interview + 5 official + 5 JD)

| source_id | type | direct URL | observed date cue / locator |
|---|---|---|---|
| src_interview_ai_engineer_15_2026 | public interview | https://www.reddit.com/r/deeplearning/comments/1swxb2k/i_did_15_ai_engineer_interviews_in_the_last_6/ | Reddit visible post date; title/body firsthand retrospective |
| src_interview_jpmorgan_ai_2026 | public interview | https://www.reddit.com/r/OfferEngineering/comments/1vgc8zk/jpmorgan_ai_engineer_phone_screen_interview/ | Reddit visible post date; title/phone-screen body |
| src_interview_google_mle_2026 | public interview | https://www.reddit.com/r/OfferEngineering/comments/1vt74g5/google_senior_mle_interview_experience_feb_2026/ | Reddit visible post date; completed interview body |
| src_interview_roblox_mle_2026 | public interview | https://www.reddit.com/r/OfferEngineering/comments/1tvu06m/roblox_mle_new_grad_interview/ | Reddit visible post date; interview body |
| src_interview_tcs_2026 | public interview | https://www.reddit.com/r/developersIndia/comments/1tyfnup/tcs_interview_experience_it_was_brutal_and_not/ | Reddit visible post date; firsthand retrospective body |
| src_interview_telus_ai_2026 | public interview | https://medium.com/@sumitpardhiya/telus-digital-ai-engineer-interview-experience-c18523996ed5 | Medium visible Published Jun 8, 2026; author/interview section |
| src_interview_guvi_ai_2026 | public interview | https://www.guvi.in/blog/ai-engineer-interview-experience/ | GUVI visible Published Feb 18, 2026; byline/interview rounds |
| src_interview_teradata_ai_2026 | public interview | https://interviewexperiences.in/experience/teradata/teradata-senior-ai-engineer-interview-experience | visible March 6, 2026; Summary/Full Experience |
| src_interview_amazon_devto_2026 | public interview | https://dev.to/saloni_jain_aba5e8c508f8a/amazon-sde-i-6-months-2026-interview-experience-off-campus-4ago | DEV visible Posted on May 30; firsthand 2026 rounds |
| src_interview_amazon_sde2_2025 | public interview | https://leetcode.com/discuss/post/7160827/ | LeetCode visible Sep 06, 2025; Round headings |
| src_official_azure_agentic_retrieval | official technical doc | https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview | updated 2026-06-12; Why use agentic retrieval |
| src_official_azure_rag_architecture | official technical doc | https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-agentic | article sections When to use / Evaluate agentic RAG |
| src_official_azure_search_intro | official technical doc | https://learn.microsoft.com/en-us/azure/search/search-what-is-azure-search | What is agentic retrieval; Choose your path |
| src_official_microsoft_foundry_rag | official technical doc | https://learn.microsoft.com/en-us/azure/foundry/concepts/retrieval-augmented-generation | HTML canonical; Last updated cue; Security and privacy considerations |
| src_official_azure_ai_search_vector | official technical doc | https://learn.microsoft.com/en-us/azure/search/vector-search-overview | Last updated cue; Vector search concepts |
| src_jd_amazon_arts_2026 | current enterprise JD | https://www.amazon.jobs/en/jobs/10451070/ai-agent-engineer-arts | Description; Basic Qualifications |
| src_jd_amazon_agentcore_2026 | current enterprise JD | https://www.amazon.jobs/en/jobs/10478272/sr-software-development-engineer-agentcore-aws-agentic-ai | job description opening |
| src_jd_amazon_ies_agents_2026 | current enterprise JD | https://www.amazon.jobs/en/jobs/10496456/software-dev-engineer-ai-agents-ies-latech | Description; responsibilities |
| src_jd_amazon_support_eval_2026 | current enterprise JD | https://amazon.jobs/en/jobs/10508294/software-development-engineer-support-agent-intelligence-and-evaluation | description; qualification |
| src_jd_amazon_principal_agentic_2026 | current enterprise JD | https://amazon.jobs/en/jobs/10508288/principal-engineer-aws-agentic-ai | live description; responsibilities |

Rejected: 4 URLs. `https://interviewing.io/` (robots/access policy), `https://www.glassdoor.com/` (login/restricted), `https://leetcode.com/discuss/post/6466098/Flexport-or-SDE-1-or-Online-Assessment-and-Interview-Experience-or-Feb-2025/` (direct fetch timeout), and `https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview?utm_source=mail` (tracking-only duplicate). No search-result, CAPTCHA, paywall, inaccessible repost, or page without a reproducible date cue was accepted.

Distribution: 10 public interview, 5 official technical, 5 current enterprise JD; 20 independent canonical URLs; all draft with empty `question_ids` by design for Task7 foreign-key population. Interview signals: 10/10 page timestamps fall in 2025-08-27..2026-08-27, and 8/10 fall in 2026-02-28..2026-08-27 (the two explicitly dated 2026-02-10 and 2026-02-20 records remain within the 365-day window); no interview event/result date is substituted for publication. Official/JD records were directly verified on 2026-08-27. Dimension coverage spans role_dim_01 through role_dim_06 across the registry.
