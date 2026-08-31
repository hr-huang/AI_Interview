# 当前代码调用链详细说明

## 1. 从哪里真正开始执行？

```python
result = pre_interview_graph.invoke(initial_state)
```

这不是调用某一个 Node，而是把控制权交给 LangGraph Runtime。

## 2. Input Processing

```text
resume_text / jd_text
        ↓
input_processing(state)
        ↓
cleaned_resume_text / cleaned_jd_text
```

Node 返回的 dict 是“局部 State 更新”，不是整个新 State。

## 3. fan-out

Input Processing 完成后，Graph 同时允许：

```text
resume_understanding
job_understanding
```

两边互不依赖。

### Resume 分支

```text
cleaned_resume_text
   ↓
resume_understanding Node
   ↓
parse_resume Service
   ↓
llm.structured(..., ResumeProfile)
   ↓
ResumeProfile
```

这版 ResumeProfile 特别保留了实习/项目内部关系，并从整份简历提取 ResumeClaim。

### Job 分支

```text
cleaned_jd_text
   ↓
job_understanding Node
   ↓
parse_job Service
   ↓
JobProfile
```

JobProfile 只表达 JD 事实，不提前做候选人能力判断。

## 4. fan-in

```text
ResumeProfile ─┐
               ├─→ competency_modeling
JobProfile ────┘
```

只有两个上游都完成，才进入建模。

## 5. CompetencyModel

```text
JobProfile
+
ResumeProfile
   ↓
LLM 语义分析
   ↓
CompetencyDraftModel
   ↓
Python 分配 competency_01 / 02 ...
   ↓
CompetencyModel
```

为什么多 Draft 一层？

因为“能力叫什么、缺什么证据”适合 LLM；而 `competency_01` 这种 ID 属于确定性基础设施，应由 Python 生成。

## 6. ClaimRegistry

```text
ResumeProfile.claims_to_verify
   ↓
纯 Python
   ↓
claim_01 / claim_02 ...
   ↓
ClaimRegistry
```

这里不调用 LLM，因为语义筛选已经在 Resume Understanding 完成。

## 7. 当前为什么结束？

当前包故意在：

```text
CompetencyModel + ClaimRegistry
```

结束。

后续才应该继续：

```text
InterviewPolicy
+
CompetencyModel
+
ClaimRegistry
   ↓
InterviewPlanner
   ↓
AssessmentTargets / time budgets / priorities
   ↓
Supervisor
   ↓
Question -> Answer -> Evidence -> LOOP
```

不要在当前阶段提前把这些层写死。
