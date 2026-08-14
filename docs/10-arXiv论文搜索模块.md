# arXiv 论文搜索模块设计文档

> 基于 arXiv.org 官方 API 的学术论文检索模块，支持关键词搜索、作者搜索、ID 查询，返回标题、作者、摘要、PDF 链接等完整信息。

---

## 1. 设计理念

**轻量零依赖**：arXiv API 免费、无需密钥，仅依赖 Python 标准库 `xml.etree.ElementTree` + 已有的 `requests`，不引入任何第三方库。

**快速路径优先**：与音乐模块相同的设计哲学——通过正则快速意图识别直接路由到 arXiv 工具，绕开 LLM 推理循环，实现毫秒级响应。

**中文友好**：支持中文分类名映射（"人工智能"→`cs.AI`、"机器学习"→`cs.LG`），关键词清理机制去除"关于"、"论文"、"研究"等冗余词，提高搜索精度。

---

## 2. 模块结构

```
modules/
├── arxiv_searcher.py       # arXiv 论文搜索模块（独立文件）
```

---

## 3. 核心架构

### 3.1 调用链路

```
用户: "搜一下arXiv上的aigc论文"
  ↓
agent_core._fast_intent 正则匹配 arXiv 意图 (Pattern 1)
  → 提取: keyword="aigc论文"
  → 清理: _clean_arxiv_keyword("aigc论文") → "aigc"
  ↓
agent_core 快速路径检测到 intent="search_arxiv"
  ↓
agent_core._execute_tool("search_arxiv", "aigc")
  ↓
arxiv_searcher.search("aigc", max_results=5)
  ├── 构造查询: ti:"aigc" OR abs:"aigc"
  ├── 请求 http://export.arxiv.org/api/query
  ├── 解析 Atom XML 响应
  └── 返回 List[Dict] 论文列表
  ↓
arxiv_searcher.format_papers(papers)
  → 格式化输出: 标题、作者、摘要、PDF链接、分类
  ↓
流式输出给用户
```

### 3.2 核心类：ArxivSearcher

| 方法 | 功能 | 参数 | 返回 |
|------|------|------|------|
| `search(keyword, max_results, category, sort_by)` | 关键词搜索 | 关键词、最大数、分类、排序 | `(bool, List[Dict] \| str)` |
| `search_by_author(author, max_results)` | 按作者搜索 | 作者名、最大数 | `(bool, List[Dict] \| str)` |
| `search_by_id(arxiv_id)` | 按 ID 查询 | arXiv ID | `(bool, Optional[Dict])` |
| `format_papers(papers)` | 格式化列表 | 论文列表 | `str` |
| `format_single_paper(paper)` | 格式化详情 | 单篇论文 | `str` |

### 3.3 支持的 arXiv 分类

| 分类代码 | 中文映射 | 领域 |
|---------|---------|------|
| `cs.AI` | 人工智能 / ai | 人工智能 |
| `cs.CL` | 自然语言 / nlp | 计算语言学 |
| `cs.CV` | 计算机视觉 / cv | 计算机视觉 |
| `cs.LG` | 机器学习 / ml | 机器学习 |
| `stat.ML` | 统计机器学习 | 统计学习 |
| `cs` | 计算机科学 | 计算机科学 |
| `physics` | 物理 | 物理学 |
| `math` | 数学 | 数学 |
| `q-bio` | 生物 | 量化生物学 |
| `eess` | 电气工程 | 电子工程 |

---

## 4. 意图识别

### 4.1 支持的触发模式

共 7 个正则模式，覆盖所有常见表达方式：

| 模式 | 匹配示例 | 提取结果 |
|------|---------|---------|
| 1. 动作词+arXiv+关键词 | "搜一下arXiv上的aigc论文" | `aigc` |
| 2. arXiv开头 | "arXiv上的最新AIGC论文" | `AIGC` |
| 3. 最新+论文 | "最新arXiv论文 LLM推理" | `LLM推理` |
| 4. XX的arXiv论文 | "diffusion的arXiv论文" | `diffusion` |
| 5. arXiv关于XX | "arXiv关于大语言模型的论文" | `大语言模型` |
| 6. 搜索+论文+关键词 | "搜索论文 扩散模型" | `扩散模型` |
| 7. arXiv论文+关键词 | "arxiv论文 自然语言处理" | `自然语言处理` |

### 4.2 作者搜索模式

| 模式 | 匹配示例 |
|------|---------|
| arXiv+作者+名字 | "arxiv作者Yoshua Bengio的论文" |
| 作者+名字+论文 | "作者Yoshua Bengio的论文" |

### 4.3 关键词清理

`_clean_arxiv_keyword()` 方法循环执行以下清理，直到关键词稳定：

1. **去除前缀**：`关于`、`对于`、`上`、`的`、`论文`、`paper` 等
2. **去除后缀**：`的论文`、`研究`、`文章` 等
3. **去除连接词**：`关于`、`对于`、`有关`
4. **空值回退**：清理后为空时，回退为 `"latest"`（搜索最新论文）

---

## 5. API 细节

### 5.1 arXiv 官方 API

```
GET http://export.arxiv.org/api/query
```

**参数**：
- `search_query`：搜索查询（支持 `ti:` 标题、`abs:` 摘要、`au:` 作者、`cat:` 分类、`id:` ID）
- `start`：起始位置
- `max_results`：最大结果数（≤ 20）
- `sortBy`：排序方式（`submittedDate` / `relevance` / `lastUpdatedDate`）
- `sortOrder`：排序方向（`ascending` / `descending`）

### 5.2 响应格式

返回 Atom XML，解析后提取：
- `title`：论文标题
- `summary`：摘要
- `authors[]`：作者列表
- `published`：发布日期
- `id`：arXiv ID
- `pdf_url`：PDF 下载链接
- `abs_url`：arXiv 详情页链接
- `categories[]`：分类代码列表

---

## 6. 错误处理

| 场景 | 处理方式 |
|------|---------|
| API 请求超时 (>15s) | 返回"搜索请求超时，请稍后重试" |
| 网络连接失败 | 返回"无法连接到 arXiv API，请检查网络" |
| 响应解析异常 | 跳过损坏条目，正常返回其他结果 |
| 关键词为空 | 返回"请告诉我要搜索什么论文" |
| 清理后关键词为空 | 回退搜索 `"latest"` 获取最新论文 |

---

## 7. 工具定义

在 `agent_core.py` 的 `TOOL_DEFINITIONS` 中注册了 3 个工具：

### 7.1 search_arxiv

```json
{
    "name": "search_arxiv",
    "description": "在 arXiv 学术论文库中搜索最新论文",
    "params": {"target": "搜索关键词，可带分类（如：cs.AI||大语言模型推理）"}
}
```

**参数格式**：`分类||关键词`（分类可选）

**示例**：
- `大语言模型` → 搜索大语言模型相关论文
- `cs.AI||扩散模型` → 在人工智能分类下搜索扩散模型
- `人工智能||推理` → 中文分类名搜索

### 7.2 arxiv_paper_detail

```json
{
    "name": "arxiv_paper_detail",
    "description": "查询指定 arXiv 论文的详细信息",
    "params": {"target": "arXiv ID"}
}
```

### 7.3 arxiv_author_search

```json
{
    "name": "arxiv_author_search",
    "description": "按作者名搜索该作者的最新论文",
    "params": {"target": "作者姓名"}
}
```

---

## 8. 使用示例

### 8.1 基础搜索

```
用户: "搜一下arXiv上的aigc论文"
AI:  [流式输出 3 篇 AIGC 论文，包含标题、作者、摘要、PDF 链接]
```

### 8.2 分类搜索

```
用户: "cs.AI分类下的最新扩散模型论文"
AI:  [流式输出 cs.AI 分类下的扩散模型论文]
```

### 8.3 作者搜索

```
用户: "arxiv作者Yoshua Bengio的论文"
AI:  [流式输出 Yoshua Bengio 的最新论文]
```

### 8.4 ID 查询

```
用户: "arXiv:2608.06366 详情"
AI:  [流式输出论文详情，包含完整摘要]
```

---

## 9. 关键设计决策

### 9.1 快速路径 vs LLM 推理

arXiv 搜索走**快速路径**而非 ReAct 循环：
- 正则直接识别意图 → 直接调用工具 → 返回结果
- 避免 LLM 延迟（~2-5s）和"编造回复"风险
- 响应时间从秒级降至百毫秒级

### 9.2 关键词清理机制

用户输入通常包含冗余词（"关于"、"论文"、"研究"等）。清理机制确保最终搜索词简洁有效：
- "关于大语言模型的论文" → "大语言模型"
- "arXiv上的最新AIGC论文" → "AIGC"
- "arxiv论文 自然语言处理" → "自然语言处理"

### 9.3 API 超时与重试

arXiv API 稳定性一般，设置 15 秒超时。失败时直接报告错误，不自动重试（由上层决定是否重试）。

### 9.4 最大结果数限制

单次最多返回 20 篇论文（API 限制），默认为 5 篇，避免信息过载。

---

## 10. 依赖说明

| 库 | 用途 | 来源 |
|------|------|------|
| `requests` | HTTP 请求 | 项目已有依赖 |
| `xml.etree.ElementTree` | Atom XML 解析 | Python 标准库 |
| `logging` | 日志记录 | Python 标准库 |

---

## 11. 扩展方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **摘要翻译** | 调用 LLM 将英文摘要翻译为中文 | 高 |
| **论文总结** | 对多篇论文进行综合总结 | 中 |
| **PDF 下载** | 自动下载论文 PDF 到本地 | 中 |
| **本地缓存** | 缓存搜索结果，避免重复请求 | 低 |
| **定时推送** | 每日自动推送最新论文 | 低 |
| **关键词订阅** | 订阅特定主题，有新论文时通知 | 低 |
