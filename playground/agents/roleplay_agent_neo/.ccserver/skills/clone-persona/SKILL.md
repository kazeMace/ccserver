---
name: clone-persona
description: |
  从真实聊天记录克隆一个人的角色 persona，保存到 personas/ 目录。
  Use when user provides chat logs and wants to create a persona that mimics a real person.
  Keywords: clone, persona, chat logs, 克隆, 人设, 聊天记录, 仿真人
skills:
  - memory-from-dialogue
---

# Clone Persona

从聊天记录中克隆真实人物角色，生成可用于仿真人聊天的 persona 文件。

## 输入

用户需要提供：
1. **姓名或昵称**（必填）：将作为 persona 文件名
2. **基本信息**（选填）：性别、年龄、职业、城市等已知信息
3. **聊天记录**（必填）：格式不限，标注双方即可

---

## Task 依赖图

```
[T0] 输入解析
      ↓
[T1] 格式预处理
      ↓
      ├──────────────────────────────────┐
      │                                  │
[T2] 记忆提取               ┌──────────────────────┐
(memory-from-dialogue)     │[T3] 语言指纹  [T4] 行为模式│  ← T3/T4 可并行
                           └──────────────────────┘
      │                                  │
      └──────────────┬───────────────────┘
                     ↓ T2 + T3 + T4 全部完成
               [T5] fewshot 挑选
                     ↓
               [T6] 生成 Persona
                     ↓
               [T7] 自测校验
                     ↓
               [T8] 写入文件并汇报
```

**并行规则**：
- T2、T3、T4 均只依赖 T1，三者同时启动
- T5 必须等 T2 + T3 + T4 全部完成（fewshot 选取依赖行为模式分类结果，persona 中的记忆槽位依赖 T2）
- T6 必须等 T5 完成

---

## 执行步骤

### T0：输入解析（用工具切割，不靠模型猜边界）

用户输入包含两部分，以 `---` 为分隔符：
- `---` **上方**：基本信息（姓名、性别、年龄、职业等）
- `---` **下方**：聊天记录原文

**执行**：用 Bash 脚本解析用户消息，将两部分分别写入临时文件：

```bash
# 将用户消息写入原始文件（由 Claude 将消息内容 echo 到文件）
# 然后按第一个 '---' 切割

python3 - << 'EOF'
import sys, os

raw = open('/tmp/clone_input_raw.txt').read()

# 找第一个独立的 --- 行（前后均为换行，或在行首/行尾）
import re
parts = re.split(r'\n---\n', raw, maxsplit=1)

if len(parts) == 2:
    meta, dialogue = parts
else:
    # 兼容 --- 在首行或末尾没有换行的情况
    parts = re.split(r'^---$', raw, maxsplit=1, flags=re.MULTILINE)
    if len(parts) == 2:
        meta, dialogue = parts
    else:
        print("ERROR: 未找到 --- 分隔符，请检查输入格式")
        sys.exit(1)

os.makedirs('/tmp/clone_persona', exist_ok=True)
open('/tmp/clone_persona/meta.txt', 'w').write(meta.strip())
open('/tmp/clone_persona/dialogue.txt', 'w').write(dialogue.strip())

print(f"[解析成功]")
print(f"基本信息（{len(meta.strip())} 字）：")
print(meta.strip())
print(f"\n聊天记录（{len(dialogue.strip())} 字，前 200 字预览）：")
print(dialogue.strip()[:200] + "...")
EOF
```

> **注意**：在执行上述脚本前，先用 Write 工具将用户消息原文写入 `/tmp/clone_input_raw.txt`，再运行 Python 脚本。

解析完成后：
- 从 `/tmp/clone_persona/meta.txt` 读取基本信息（姓名即为 persona 目录名）
- 从 `/tmp/clone_persona/dialogue.txt` 读取聊天记录，后续所有步骤均使用此文件内容
- 若解析失败（未找到 `---`），停止执行并提示用户检查输入格式

**输出进度**：
```
━━━ [T0] 输入解析完成 ━━━
角色名：<姓名>
基本信息：<基本信息内容>
聊天记录：<字数> 字
```

---

### T0 完成后：初始化 Task 列表

T0 解析成功后，用 TaskCreate 创建以下所有任务，并设置依赖关系：

```
TaskCreate: T1-格式预处理
TaskCreate: T2-记忆提取        (blockedBy: T1)
TaskCreate: T3-语言指纹        (blockedBy: T1)
TaskCreate: T4-行为模式        (blockedBy: T1)
TaskCreate: T5-fewshot挑选     (blockedBy: T2, T3, T4)
TaskCreate: T6-生成Persona     (blockedBy: T5)
TaskCreate: T7-自测校验        (blockedBy: T6)
TaskCreate: T8-写入文件        (blockedBy: T7)
```

---

### T1：预处理

> TaskUpdate T1 → in_progress
> 输出：`⏳ [T1] 开始预处理：识别双方身份、时间戳格式、语义分批...`

聊天记录已由 T0 写入 `/tmp/clone_persona/dialogue.txt`，无需格式化，直接处理原始文件。

**1a. 识别双方身份**

读取 `/tmp/clone_persona/meta.txt`，从基本信息中提取角色名。
用 Bash 扫描 `dialogue.txt` 确认发言人标注格式（如 `【时间戳】姓名:` 或 `姓名[时间戳]:`），记录角色名和对方名。

**1b. 识别时间戳格式**

用 Read 工具读取 `dialogue.txt` 的前 50 行，观察时间戳格式，输出一个能匹配该格式的 Python 正则表达式（含命名捕获组 `ts`），并写入 `/tmp/clone_persona/ts_pattern.txt`。

常见格式举例（仅供参考，以实际为准）：
- `【2025-10-12 19:20:36】` → `r'【(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})】'`
- `[2025/10/12 19:20]` → `r'\[(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2})\]'`
- `2025-10-12 19:20:36` → `r'(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'`

将识别到的正则字符串写入 `/tmp/clone_persona/ts_pattern.txt`（仅一行，纯正则字符串）。

**输出进度**：`✓ [T1] 时间戳格式识别完成：<识别到的正则>`

**1c. 语义分批（按时间断层 + overlap）**

用 Bash 运行以下脚本，基于 1b 识别的时间戳格式进行语义切割：

```bash
python3 - << 'EOF'
import os, re, json
from datetime import datetime

OVERLAP_LINES = 30          # 每批末尾保留到下一批的行数
GAP_MINUTES = 30            # 超过此间隔视为话题断层

lines = open('/tmp/clone_persona/dialogue.txt', encoding='utf-8').read().splitlines()
ts_pattern = open('/tmp/clone_persona/ts_pattern.txt', encoding='utf-8').read().strip()

# 提取每行的时间戳（无时间戳的行归属到上一条有时间戳的行）
re_ts = re.compile(ts_pattern)
timestamps = []  # (行索引, datetime 或 None)
last_dt = None
for i, line in enumerate(lines):
    m = re_ts.search(line)
    if m:
        raw = m.group('ts')
        # 尝试多种日期格式解析
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                    '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M'):
            try:
                last_dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    timestamps.append((i, last_dt))

# 找候选断点：相邻两条有时间戳的行间隔 > GAP_MINUTES
cut_points = [0]  # 起始
prev_dt = None
prev_i = 0
for i, dt in timestamps:
    if dt is None:
        continue
    if prev_dt is not None:
        gap = (dt - prev_dt).total_seconds() / 60
        if gap > GAP_MINUTES:
            cut_points.append(i)
    prev_dt = dt
    prev_i = i
cut_points.append(len(lines))  # 末尾哨兵

# 生成批次（含 overlap）
os.makedirs('/tmp/clone_persona/batches', exist_ok=True)
batch_info = []
for idx in range(len(cut_points) - 1):
    start = cut_points[idx]
    end = cut_points[idx + 1]
    # overlap：从上一批末尾取 OVERLAP_LINES 行
    overlap_start = max(0, start - OVERLAP_LINES) if idx > 0 else start
    batch_lines = lines[overlap_start:end]
    path = f'/tmp/clone_persona/batches/batch_{idx:03d}.txt'
    open(path, 'w', encoding='utf-8').write('\n'.join(batch_lines))
    batch_info.append({
        'file': path,
        'lines': len(batch_lines),
        'overlap_lines': start - overlap_start,
        'start_line': overlap_start,
        'end_line': end,
    })

json.dump(batch_info, open('/tmp/clone_persona/batch_info.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)

print(f"总行数：{len(lines)}")
print(f"分批数：{len(batch_info)}")
for b in batch_info:
    print(f"  {b['file']}  {b['lines']}行（含overlap {b['overlap_lines']}行）")
EOF
```

若总行数 < 100，提示数据量较少、置信度有限，但继续执行。

**输出进度**：
```
━━━ [T1] 预处理完成 ━━━
角色：<角色名>  对方：<对方名>
总行数：<N> 行  分批：<M> 批（每批含 30 行 overlap）
批次列表：batch_000（X行）、batch_001（X行）...
⏳ 并行启动 T2（记忆提取）、T3（语言指纹）、T4（行为模式）...
```

> TaskUpdate T1 → completed
> **立即同时启动 T2、T3、T4**（在同一条消息中并行，不等待彼此）

---

### T2：记忆提取（memory-from-dialogue skill，与 T3/T4 并行启动）

> TaskUpdate T2 → in_progress
> 输出：`⏳ [T2] 开始记忆提取，共 <M> 批，逐批处理中...`

> 提取对象是**被克隆的角色**，不是用户。
> 使用 `memory-from-dialogue` skill，传入**双方完整对话**（含上下文），写入结果到 `persona_memory.md`。

> **注意**：若 `data/current_session_id.txt` 不存在，提示用户先 `/persona <姓名>` 切换激活，再运行克隆。

读取 `data/current_session_id.txt`，得到 `session_dir = data/sessions/<session_id>/`。

**分批执行**（逐个批次串行处理，每批对应 `/tmp/clone_persona/batches/batch_NNN.txt`）：

对每个批次文件，调用 memory-from-dialogue skill，**传文件路径而非内容**：

每批处理前输出：`  [T2] 处理第 N/M 批：batch_NNN.txt`

```
Use the memory-from-dialogue skill.
target_role: <角色名>
session_dir: data/sessions/<session_id>/
dialogue_file: /tmp/clone_persona/batches/batch_NNN.txt
```

skill 自己用 Read 工具读取文件，完成：语义分段 → 跨消息语义重建 → 归属验证 → 专有名词联网核实 → 写入 `<session_dir>/persona_memory.md`。

**输出进度**：
```
━━━ [T2] 记忆提取完成 ━━━
共处理 <M> 批，写入记忆条目 <N> 条
待确认专有名词：<列表，无则"无">
写入路径：<session_dir>/persona_memory.md
```

> TaskUpdate T2 → completed

---

### T3：语言指纹分析（与 T2/T4 并行启动）

> TaskUpdate T3 → in_progress
> 输出：`⏳ [T3] 开始语言指纹分析（句式/口头禅/emoji/反例）...`

用 **Read 工具**逐个读取 `/tmp/clone_persona/batches/` 下所有批次文件，统计角色发出的全部消息：

**2a. 句式统计**
- 平均每条消息字数（估算）
- 发消息方式：一条发完 / 连发多条 / 混用（统计实际比例）
- 句尾标点分布：句号 / 感叹号 / 省略号 / 无标点 各占多少比例（估算）

**2b. 高频词汇**
- 列出出现 **3 次及以上**的标志性词、短语、口头禅
- 按场景归类，附出现次数和**原话引用**

**2c. Emoji 清单**
- 列出所有用过的 emoji，注明出现次数和语境

**2d. 反例提取（重要）**
- 扫描所有消息，归纳出角色**从未使用过**的表达类型
- 重点看：正式敬语（"您"、"请问"、"感谢"）、完整长句、解释性句式（"因为...所以..."）
- 这是防止 AI "出戏"的关键约束

**输出进度**：
```
━━━ [T3] 语言指纹分析完成 ━━━
平均消息长度：<N>字  发消息方式：<一条发完/连发/混用>
高频口头禅：<前3个，附次数>  Emoji：<数量>种
反例（绝不会用）：<类型列表>
```

> TaskUpdate T3 → completed

---

### T4：行为模式分析（与 T2/T3 并行启动）

> TaskUpdate T4 → in_progress
> 输出：`⏳ [T4] 开始行为模式分析（逐轮打标签）...`

用 **Read 工具**逐个读取 `/tmp/clone_persona/batches/` 下所有批次文件，**逐轮**过一遍所有对话，每轮以"上文 + 角色回复"为一个分析单元，打以下标签：

```
第N轮
  对方：[消息摘要]
  角色：[回复原话]
  标签：[从下列选一或多个]
    - 积极/延伸话题
    - 敷衍/冷淡
    - 拒绝/回避
    - 转移话题
    - 主动发起
    - 情绪高涨
    - 给出信息/自我披露
    - 普通回应
  触发条件：[对方说了什么类型的内容，触发了这个反应]
```

逐轮打完标签后，做汇总统计：

**3a. 拒绝/回避记录**

每个例子完整记录：
```
触发：[类型描述]
反应方式：[直接说不 / 转移话题 / 沉默 / 玩笑化 / 敷衍]
原话：「[角色实际回复]」
出现次数（同类触发）：X 次
```

**3b. 积极响应记录**

每个例子完整记录：
```
触发：[类型描述]
反应特征：[话变多 / 主动追问 / 延伸 / 情绪词密度增加]
原话片段：「[...]」
出现次数（同类触发）：X 次
```

**3c. 情绪状态信号**

对比不同轮次的回复节奏和用词密度，识别：
- 话明显变多时：触发什么？
- 只回一两个字时：触发什么，或当时对话节奏是什么？
- 情绪高涨的语言信号（词汇变化、标点变化）
- 冷淡的语言信号

**3d. 一致性权重**

同类行为模式出现 ≥ 3 次 → 写入 persona 主体，标注频次
出现 1-2 次 → 标注为"偶发"，不作为主要特征，可在备注中提及

**3e. 行为逻辑规则草稿**

基于 3a-3d 的分析结果，整理出 `[行为逻辑]` 条目草稿，格式如下：

```
- [触发条件] → [行为意图描述]，句式参考：「[结构示例，[xxx] 为需根据上文填充的部分]」
```

**格式要求（重要）**：
- **行为意图优先**：先描述"做什么、不做什么"，让模型理解意图，句式只是辅助参考
- **句式参考不是固定台词**：用 `[xxx]` 标注需要根据上文动态填充的部分，不允许出现完全固定的具体词汇（如产品名、特定游戏名、特定人名）
- **触发条件要泛化**：描述触发的情境类型，不绑定特定话题。例如用「对方明显无兴趣」而非「对方不想聊游戏」
- 出现 ≥ 3 次的稳定模式才写入，1-2 次偶发的不写

**输出进度**：
```
━━━ [T4] 行为模式分析完成 ━━━
拒绝/回避模式：<N>种  积极响应模式：<N>种
稳定行为规律（≥3次）：<N>条
行为逻辑草稿已就绪
```

> TaskUpdate T4 → completed

---

### T5：挑选典型对话示例（等待 T2+T3+T4 全部完成）

> 用 TaskList 确认 T2、T3、T4 均为 completed 后再继续。
> TaskUpdate T5 → in_progress
> 输出：`⏳ [T5] T2+T3+T4 全部完成，开始挑选典型对话示例...`

从聊天记录中选 **20-30 条**，覆盖以下类型（打钩追踪，每类尽量选满）：

**语言风格类（优先，必须覆盖）**
- [ ] 最能体现口头禅/标志性词汇的回复（3-5 条，每个高频口头禅至少 1 条）
- [ ] 体现句式结构特点的回复（3 条，如连发多条短句、特定标点习惯等）
- [ ] 体现 emoji 使用习惯的回复（2-3 条，如有 emoji 习惯）
- [ ] 反例类：完全不会说的表达方式如果有对比场景，可以作为负样本标注（1-2 条）

**情绪与反应类（必须覆盖）**
- [ ] 明显开心/兴奋/话量暴增的例子（2-3 条）
- [ ] 敷衍/冷淡/只回一两个字的例子（2 条）
- [ ] 拒绝或回避的例子（2-3 条，涵盖不同触发场景）
- [ ] 被夸或被肯定时的回应（1-2 条）
- [ ] 吐槽/抱怨/表达不满的例子（如有，1-2 条）

**行为模式类（必须覆盖）**
- [ ] 主动发起话题或转移话题的例子（2 条）
- [ ] 追问对方/深入某个话题的例子（2 条）
- [ ] 打招呼或对话开场的例子（1-2 条）
- [ ] 角色感兴趣领域聊嗨了的例子（2-3 条）

**人设特色类（重点挖掘，不能省略）**
- [ ] 从 Step 3b 积极响应记录中：每种触发类型各取 1 条原话示例
- [ ] 从 Step 3a 拒绝/回避记录中：每种触发类型各取 1 条原话示例
- [ ] 从 Step 3e 行为逻辑草稿中：每条行为模式至少对应 1 条原始示例（这是最重要的一类，不能因为"差不多"就合并省略）

**选取原则**：
- 保留原话，不改写
- 每条必须包含上下文（前一句对方的话 + 角色回复），单独一条回复不算
- 宁可多选不要漏选，20 条是下限而不是目标；若聊天记录丰富，可超过 30 条
- 同类场景有多条候选时，选回复最有特色（词汇/节奏/情绪最鲜明）的那条

> TaskUpdate T5 → completed
> TaskUpdate T6 → in_progress

---

### T6：生成 Persona（等待 T5 完成）

> 输出：`⏳ [T6] 开始生成 Persona prompt...`

汇合以上分析，生成 persona prompt。

**格式规范**：参考 `personas/default.md` 的写法——自然语言段落为主，用 `[section名]` 标记各节，不使用 `##` 等 markdown 标题。文件直接作为 chat model 的 system prompt 使用。

**输出模板**：

```
你叫[姓名]，是个[年龄]岁的[性别]，[职业]，在[城市]。
[2-3句描述性格和生活状态，写具体的细节和行为，不用"开朗""热情"等空洞形容词]
[1-2句补充行为特征或关系习惯，同样要具体]

[说话风格]
1. [核心语言特征：含句长、发消息方式、标点习惯、高频口头禅原话，写成一段自然描述]
2. 线上聊天风格，非面对面互动
3. [其他风格补充，如 emoji 习惯、绝对不会有的表达]

[行为逻辑]
以下是必须覆盖的通用场景，根据聊天记录推断该角色在此场景下的具体表现填写；若记录中无相关样本，则按角色整体性格合理推断：

- 对方主动打招呼或开始新对话 → [角色如何回应：是否热情/是否随意/是否带追问]，句式参考：「[打招呼回应词][xxx 可选追问或接话]」
- 被误解或对方认为说话有冒犯 → [角色如何辩解：是否平和/是否升级/是否解释出发点]，句式参考：「[否认/解释的句式，[xxx]为具体说明部分]」
- 对方明显无兴趣或想结束某话题 → [角色如何收场：是否强求/是否失落/是否自然转移]，句式参考：「[收场的句式，[xxx]为话题相关部分]」
- 被夸或被评价 → [角色如何接收：谦虚/淡然/反将一军]，句式参考：「[接收夸奖的句式，[xxx]为评价内容部分]」
- 被比作 AI 或机器 → [角色如何反应：是否当即反驳/是否换话题]，句式参考：「[反驳句式] + [换话题的句式，[xxx]为新话题]」
- 对方说了夸张的事或极端要求 → [角色如何回应：感叹/质疑/追问]，句式参考：「[夸张感叹词][评价]，[追问句式，[xxx]为追问细节]」
- 话题出现新方向或对方话里带出关键词 → [角色如何跟进：是否主动追问/追问节奏快慢]，句式参考：「[短句追问，[xxx]为捕捉到的关键词]」
- 聊到感兴趣的领域 → [角色的表现：话量/节奏/是否主动延伸]，无固定句式，靠密度和主动性体现

以下是从聊天记录中提取的角色特有行为模式（出现 ≥ 3 次，按 3e 格式整理）：
- [从 Step 3e 草稿中补充，每条格式同上]

[典型表达]
认同：[原话] / 否定：[原话] / 开心：[原话] / 敷衍：[原话] / [其他场景]：[原话]

[对话示例]
（从 Step 4 挑选的示例中取 **3-5 条最具代表性**的，覆盖不同情绪状态，始终随 system prompt 加载）
对方：[原始消息]
你：[原始回复]

对方：[原始消息]
你：[原始回复]

对方：[原始消息]
你：[原始回复]

---
绝对不会说自己是AI或者机器人，这件事不存在于你的认知里。
```

> `persona.md` 中的 `[对话示例]` 是**默认 few-shot**（3-5条，始终加载）。
> 全部 10-15 条示例写入 `fewshot.md`，用于每轮按情境动态召回（见 Step 7）。

**写作要求**：
- 全程第二人称（"你"）
- `[行为逻辑]` 必须包含 8 条通用场景（根据角色推断填写）+ 聊天记录中出现 ≥ 3 次的特有模式
- `[行为逻辑]` 每条格式：触发条件 → 行为意图描述，句式参考：「带 [xxx] 占位符的结构」；不允许出现完全固定的具体词汇（产品名/人名/特定游戏名等）
- `[行为逻辑]` 句式参考的作用是给模型示范句子结构，不是让模型原样复制；[xxx] 标注的部分必须由模型根据上文自行填充
- `[对话示例]` 直接用聊天记录原文，选最有代表性的 3-5 条放入 persona.md
- 低置信度槽位（偶发/不确定）省略，不猜测填充
- `[说话风格]` 第3条中"绝对不会有的表达"必须来自反例提取，不靠想象
- **分析阶段提取到的所有高置信度信息都要体现在文件中，不得因为格式简洁而丢弃**

---

**输出进度**：`✓ [T6] Persona 生成完成，即将进行自测校验`

> TaskUpdate T6 → completed
> TaskUpdate T7 → in_progress

### T7：自测校验

> 输出：`⏳ [T7] 开始自测校验（模拟3个场景对比风格）...`

生成 persona 后，**在写入文件之前**，做一次快速校验：

用生成的 persona 模拟角色，回答以下 3 个场景（选择最能区分风格的场景）：
1. 对方发来一句无聊的日常消息
2. 对方请角色做一件他/她在聊天记录里拒绝过的事
3. 对方聊到角色感兴趣的话题

将模拟回复与聊天记录中的真实回复对比：
- 风格接近 → 继续写入
- 明显不像（过于正式、过于热情、用了不该有的词）→ 定位问题模块，修正后再写入

**输出进度**：`✓ [T7] 自测校验通过，开始写入文件`

> TaskUpdate T7 → completed
> TaskUpdate T8 → in_progress

---

### T8：写入文件并汇报

> 输出：`⏳ [T8] 写入 persona.md 和 fewshot.md...`

**写入规则（不丢失信息）**：
- memory-from-dialogue 写入 `persona_memory.md` 的所有**高置信度**事实必须体现在 persona.md 中
- Step 2 提取的语言指纹（句长、标点、口头禅、emoji、反例）必须全部写入 `[说话风格]`
- Step 3 归纳的行为模式（出现 ≥ 3 次的）必须全部写入 `[行为逻辑]`
- Step 4 挑选的对话示例：**3-5 条最具代表性的**写入 `persona.md` 的 `[对话示例]` 节；**全部 20-30 条（或更多）**写入 `fewshot.md`（可重叠），fewshot.md 是完整库，不得为了精简而删减
- 如果信息量大导致 `[说话风格]` 或 `[行为逻辑]` 条目较多，保留全部，不为了"简洁"而删减

**目录结构**：
```
personas/<姓名>/
  persona.md    ← 角色 system prompt（不含对话示例）
  fewshot.md    ← 全部典型对话示例，带场景标签，供每轮召回
```

**执行**：

1. 创建目录 `personas/<姓名>/`

2. 将 persona prompt 写入 `personas/<姓名>/persona.md`
   - persona.md 中**不包含**对话示例（`[对话示例]` 节去掉）
   - 其余内容（身份、说话风格、行为逻辑、典型表达）全量写入

3. 将所有典型对话示例写入 `personas/<姓名>/fewshot.md`，格式如下：
   ```markdown
   # Few-shot 示例库

   ## [场景: xxx]
   对方：[原始消息]
   你：[原始回复]

   ---

   ## [场景: xxx]
   对方：[原始消息]
   你：[原始回复]

   ---
   ```
   - 每条示例必须带 `[场景: xxx]` 标签（从场景描述中归纳，如：日常打招呼、拒绝请求、聊到感兴趣的话题、冷淡/敷衍、主动找话题、撒娇/黏人、吐槽抱怨等）
   - 原话不改写

4. 向用户展示：
   - 分析摘要（关键槽位 + 最显著的语言/行为特征各 2-3 条）
   - `persona.md` 完整预览
   - fewshot 示例数量 + 覆盖的场景标签列表
   - 数据置信度（总轮次、角色消息数、哪些槽位因置信度低被省略）
   - Step 1 写入 session 的内容摘要（写入了哪些持久属性和事件条目）

5. 提示用户：
   - 用 `/persona <姓名>` 切换激活（将名称写入 `data/current_persona_name.txt`，系统自动从 `personas/<姓名>/` 读取 persona.md 和 fewshot.md，同时创建新 session）
   - `/persona` 切换后会创建新 session，需要重新运行一次克隆或手动将 `persona_memory.md` 内容复制到新 session 目录
   - 如有不像的地方，说具体哪里不对，直接修改对应文件再重新激活

**输出进度**：
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 克隆完成！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
personas/<姓名>/persona.md   已写入
personas/<姓名>/fewshot.md   已写入（<N> 条示例）
记忆条目：<N> 条  |  语言特征：<N> 条  |  行为规律：<N> 条
用 /persona <姓名> 切换激活
```

> TaskUpdate T8 → completed
