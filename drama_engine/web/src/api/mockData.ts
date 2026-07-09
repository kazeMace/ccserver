// 原型 5 个游戏脚本移植（data.jsx → TS）。
// 保留原型的 step 结构（一批消息 + 可选 reply），adapter 层再转成 interaction.v1。
// 这些是端点未就绪期的演示数据；v1 就绪后不再使用。

export interface MockMsg {
  kind: "announce" | "narrate" | "chat" | "vn" | "clue" | "secret" | "affinity" | "media";
  text?: string;
  strong?: boolean;
  self?: boolean;
  sender?: { type: string; id: string; name: string; emoji: string; role?: string };
  roleTag?: string;
  name?: string;
  emoji?: string;
  title?: string;
  desc?: string;
  foot?: string;
  head?: string;
  body?: string;
  dir?: "up" | "down";
  media?: {
    kind: "video" | "audio" | "image";
    title?: string;
    url: string;
    poster?: string;
    subtitleUrl?: string;
    autoplay?: boolean;
  };
}

export interface MockReply {
  type: "text" | "choice" | "choice_or_text" | "vote" | "structured" | "form" | "confirm";
  prompt: string;
  who?: string;
  placeholder?: string;
  label?: string;
  timer?: number;
  widget?: string;
  props?: Record<string, unknown>;
  options?: { id: string; text: string; desc?: string; cond?: string; disabled?: boolean }[];
  candidates?: { id: string; name: string; emoji: string; count?: number }[];
  fields?: { id: string; label: string; type: string; min?: number; max?: number; value?: number; placeholder?: string }[];
}

export interface MockStep {
  channel: string;
  msgs: MockMsg[];
  reply?: MockReply;
}

export interface MockGame {
  genre: string;
  roles: { seat_id: string; name: string; emoji?: string; role?: string; controller?: "human" | "ai" | "system"; description?: string }[] | Record<string, { name: string; display_name?: string; description?: string; portrait_url?: string; emoji?: string; voice_id?: string; faction?: string }>;
  channels: { id: string; name: string; icon: string; lock?: boolean; badge?: number }[];
  players: { id: string; name: string; emoji: string; tag?: string; tagText?: string; online?: boolean; dead?: boolean }[];
  phase: string;
  stats?: { icon: string; name: string; value: string }[];
  affinities?: { id: string; name: string; emoji: string; value: number; max: number }[];
  circles?: { name: string; emoji: string; members: string }[];
  progress?: { label: string; cur: number; total: number };
  boardState?: string[];
  steps: MockStep[];
  channelContent?: Record<string, MockMsg[]>;
}

export const GAMES = [
  { id: "werewolf", icon: "🐺", name: "狼人杀", sub: "9 人标准局 · 社交推理", tip: "狼人杀" },
  { id: "the_clause", icon: "📜", name: "The Clause", sub: "文字冒险 · 合约恋情", tip: "文字冒险" },
  { id: "mystery", icon: "🕯️", name: "午夜庄园", sub: "剧本杀 · 6 人硬核本", tip: "剧本杀" },
  { id: "variety", icon: "🏝️", name: "恋爱岛", sub: "综艺 AI · 7 天节目", tip: "综艺 AI" },
  { id: "board", icon: "⚫", name: "五子棋", sub: "桌游 · 人机对弈", tip: "桌游" },
];

// —— 便捷构造器（对齐原型）——
const sys = (text: string, opt: Partial<MockMsg> = {}): MockMsg => ({ kind: "announce", text, ...opt });
const narr = (text: string): MockMsg => ({ kind: "narrate", text });
const chat = (sender: MockMsg["sender"], text: string, opt: Partial<MockMsg> = {}): MockMsg => ({ kind: "chat", sender, text, ...opt });
const vn = (name: string, emoji: string, text: string): MockMsg => ({ kind: "vn", name, emoji, text });
const clue = (title: string, desc: string, foot: string): MockMsg => ({ kind: "clue", title, desc, foot });
const secret = (head: string, body: string): MockMsg => ({ kind: "secret", head, body });
const affin = (text: string, dir: "up" | "down" = "up"): MockMsg => ({ kind: "affinity", text, dir });
const me = (text: string): MockMsg => ({ kind: "chat", sender: { type: "player", id: "me", name: "你", emoji: "😎" }, text, self: true });
const media = (kind: NonNullable<MockMsg["media"]>["kind"], title: string, url: string, poster?: string, subtitleUrl?: string): MockMsg => ({
  kind: "media",
  text: title,
  media: { kind, title, url, poster, subtitleUrl, autoplay: false },
});

const WEREWOLF: MockGame = {
  genre: "werewolf",
  roles: [
    { seat_id: "p1", name: "你", emoji: "😎", role: "预言家", controller: "human", description: "真人玩家，负责关键发言和投票。" },
    { seat_id: "p2", name: "阿哲", emoji: "🧑", role: "村民", controller: "ai" },
    { seat_id: "p3", name: "小美", emoji: "👧", role: "村民", controller: "ai" },
    { seat_id: "p4", name: "老王", emoji: "👨", role: "狼人", controller: "ai" },
    { seat_id: "p5", name: "莉莉", emoji: "👩", role: "村民", controller: "ai" },
    { seat_id: "p6", name: "大熊", emoji: "🧔", role: "村民", controller: "ai" },
    { seat_id: "p7", name: "青青", emoji: "👱‍♀️", role: "狼人", controller: "ai" },
    { seat_id: "p8", name: "阿力", emoji: "👦", role: "女巫", controller: "ai" },
    { seat_id: "p9", name: "花花", emoji: "👩‍🦰", role: "狼人", controller: "ai" },
  ],
  channels: [
    { id: "public", name: "全场", icon: "🏛️" },
    { id: "wolf", name: "狼人频道", icon: "🐺", lock: true },
  ],
  players: [
    { id: "me", name: "你", emoji: "😎", tag: "me", tagText: "预言家", online: true },
    { id: "p2", name: "阿哲", emoji: "🧑", online: true },
    { id: "p3", name: "小美", emoji: "👧", online: true },
    { id: "p4", name: "老王", emoji: "👨", online: true },
    { id: "p5", name: "莉莉", emoji: "👩", online: true },
    { id: "p6", name: "大熊", emoji: "🧔", online: true, dead: true, tagText: "昨夜出局" },
    { id: "p7", name: "青青", emoji: "👱‍♀️", online: true },
    { id: "p8", name: "阿力", emoji: "👦", online: true },
    { id: "p9", name: "花花", emoji: "👩‍🦰", online: true },
  ],
  phase: "第 2 天 · 白天",
  steps: [
    { channel: "public", msgs: [sys("🌙 夜晚降临，天黑请闭眼。", { strong: true }), sys("预言家、狼人请在各自频道行动。")] },
    {
      channel: "public",
      msgs: [
        sys("☀️ 天亮了。昨晚，大熊 倒在了血泊之中。", { strong: true }),
        sys("现在进入自由讨论环节，请依次发言，找出隐藏的狼人。"),
        chat({ type: "player", id: "p2", name: "阿哲", emoji: "🧑" }, "昨晚大熊出局，我觉得刀法很像冲着好人核心去的。我先起跳，我是村民。"),
        chat({ type: "player", id: "p3", name: "小美", emoji: "👧" }, "我怀疑老王，他昨天发言一直在带节奏。"),
      ],
    },
    {
      channel: "public",
      msgs: [chat({ type: "player", id: "p4", name: "老王", emoji: "👨" }, "别乱咬啊小美！我要是狼会这么明显？我看你才有问题。")],
      reply: { type: "text", prompt: "轮到你发言了", who: "你 · 预言家（未跳）", placeholder: "组织你的发言……要不要报验人结果？" },
    },
    {
      channel: "public",
      msgs: [
        me("我是预言家！昨晚我验了 阿哲，结果是【金水】好人。今天建议大家投票压 老王，他刚才急着反咬的样子很心虚。"),
        chat({ type: "player", id: "p5", name: "莉莉", emoji: "👩" }, "预言家跳出来了！那我跟你，压老王。"),
        chat({ type: "player", id: "p4", name: "老王", emoji: "👨" }, "……我也是预言家！我验的你才是狼！"),
        sys("出现双预言家对跳，请全场投票放逐一名玩家。"),
      ],
      reply: {
        type: "vote",
        prompt: "投票放逐 —— 选择你怀疑的玩家",
        who: "你 · 预言家",
        timer: 28,
        widget: "day_exile",
        props: { show_vote_count: true },
        candidates: [
          { id: "p2", name: "阿哲", emoji: "🧑" },
          { id: "p3", name: "小美", emoji: "👧" },
          { id: "p4", name: "老王", emoji: "👨", count: 3 },
          { id: "p5", name: "莉莉", emoji: "👩" },
          { id: "p7", name: "青青", emoji: "👱‍♀️" },
          { id: "p8", name: "阿力", emoji: "👦" },
          { id: "p9", name: "花花", emoji: "👩‍🦰", count: 1 },
        ],
      },
    },
    {
      channel: "public",
      msgs: [
        sys("投票结束：老王 以 5 票被放逐出局。", { strong: true }),
        sys("🎴 老王的身份是——狼人！好人阵营士气大振。"),
        narr("夜幕再次降临，未完待续……"),
      ],
    },
  ],
  channelContent: {
    wolf: [
      sys("🐺 狼人频道 · 仅同伴可见", { strong: true }),
      chat({ type: "player", id: "p7", name: "青青", emoji: "👱‍♀️", role: "狼" }, "今晚刀谁？我觉得刀大熊，他像预言家。", { roleTag: "狼同伴" }),
      chat({ type: "player", id: "p9", name: "花花", emoji: "👩‍🦰", role: "狼" }, "同意，刀大熊。明天我悍跳预言家咬阿哲。", { roleTag: "狼同伴" }),
    ],
  },
};

const THE_CLAUSE: MockGame = {
  genre: "galgame",
  roles: {
    nora: {
      name: "Nora Hampton",
      description: "30 岁的精英律师。聪明、独立、坚强，但因母亲的医药费陷入财务困境。表面冷静，内心敏感。",
      portrait_url: "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598/0/image/release/r_84b4ba46/character/72965/avatar_306fdcd6-d86d-4a4d-abe1-4935731f9d39.webp",
      emoji: "⚖️",
      voice_id: "en-US-JennyNeural",
      faction: "protagonist",
    },
    marco: {
      name: "Marco Diaz",
      description: "35 岁的亿万富翁、冷酷商人。掌控欲强，习惯用钱解决问题，但内心深处渴望真实的感情。",
      portrait_url: "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598/0/image/release/r_84b4ba46/character/28296/avatar_7965866d-09ec-4d36-8a94-345ac7cd1915.webp",
      emoji: "🕴️",
      voice_id: "en-US-GuyNeural",
      faction: "male_lead",
    },
    leila: {
      name: "Leila",
      description: "Nora 的闺蜜兼同事，28 岁。外向、乐观，总是支持 Nora 的决定。",
      portrait_url: "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598/0/image/release/r_84b4ba46/character/63588/avatar_8851ca73-7c50-4675-ba44-f9e2dff9f8eb.webp",
      emoji: "👭",
      voice_id: "en-US-AriaNeural",
      faction: "friend",
    },
    nick: {
      name: "Nick",
      description: "Nora 的前男友，32 岁律师。自负、爱面子，对 Nora 还有感情但不愿承认。",
      portrait_url: "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598/0/image/release/r_84b4ba46/character/5071/avatar_f7b74e09-9851-4059-9013-56f95ed6af07.webp",
      emoji: "💼",
      voice_id: "en-US-AndrewNeural",
      faction: "ex_boyfriend",
    },
    seth: {
      name: "Seth",
      description: "Marco 的商业对手，38 岁。阴险、狡猾，想搞垮 Marco 的 IPO。",
      portrait_url: "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598/0/image/release/r_84b4ba46/character/30082/avatar_b54b4ce7-3eff-4646-9f51-f59e0e882f65.webp",
      emoji: "😈",
      voice_id: "en-US-DavisNeural",
      faction: "antagonist",
    },
  },
  channels: [{ id: "story", name: "主线", icon: "📖" }],
  players: [
    { id: "Player_1", name: "Nora Hampton", emoji: "⚖️", tag: "me", tagText: "玩家", online: true },
  ],
  phase: "Chapter 1 · The Offer",
  stats: [
    { icon: "⚖️", name: "职业判断", value: "7" },
    { icon: "🖋️", name: "谈判锋芒", value: "5" },
    { icon: "🔥", name: "情绪克制", value: "4" },
  ],
  affinities: [
    { id: "marco", name: "Marco Diaz", emoji: "🕴️", value: 0, max: 100 },
  ],
  steps: [
    {
      channel: "story",
      msgs: [
        media(
          "video",
          "第一幕 · 初遇",
          "https://assets.castloop.ai/xnarrator-game/editor-beta/1780365598/0/video/dialogue/74937/upload_af35ffad-3351-430a-9772-9fac13b1101e.mp4",
          "",
          "",
        ),
        narr("律所会议室。你是 Nora Hampton，刚刚输掉了一场本不该输的官司。"),
        narr("Marco Diaz 在夜里收购了你的委托人，让你的胜诉策略瞬间失效。傍晚，他的车停在你公寓楼下等你。"),
      ],
      reply: {
        type: "choice_or_text",
        prompt: "Marco 的车门已经打开。你要怎么做？",
        who: "Nora Hampton",
        placeholder: "自由输入：例如质问他、冷处理，或直接上车……",
        options: [
          { id: "get_in_car", text: "上车", desc: "大胆面对 Marco 的提案 · Marco 好感 +5" },
          { id: "ignore_walk", text: "不理他，继续往前走", desc: "保持距离，但你仍会听见那份合约 · Marco 好感 +1" },
        ],
      },
    },
    {
      channel: "story",
      msgs: [
        me("我会上车，但只给你十分钟解释。"),
        affin("Marco Diaz 对你的兴趣 +5 ❤️"),
        vn("Marco Diaz", "🕴️", "十分钟足够。Nora，我需要一个聪明、锋利、并且足够让投资人相信的未婚妻。"),
        narr("车内，Marco 提出合约：假装恋人，一起出席投资人晚宴。你看见合约末尾还留着一条空白条款。"),
      ],
      reply: {
        type: "choice_or_text",
        prompt: "你会如何处理那条空白条款？",
        who: "选择将映射到 The Clause 的分支",
        placeholder: "自由输入：写下你想加进合约的条款……",
        options: [
          { id: "dont_say", text: "什么都不说", desc: "保守接受 · Marco 好感 +1" },
          { id: "negotiate_clause", text: "再谈判一条新条款", desc: "强硬谈判 · Marco 好感 +5" },
        ],
      },
    },
    {
      channel: "story",
      msgs: [
        me("我要加一条：任何公开亲密行为，都必须经过我同意。"),
        affin("Marco Diaz 对你的兴趣 +5 ❤️"),
        vn("Marco Diaz", "🕴️", "很好。会谈判的人才值得签约。"),
        narr("次日，顶层公寓。造型师为你试装，Marco 亲手为你戴上一只纯金手镯。"),
      ],
      reply: {
        type: "choice_or_text",
        prompt: "晚宴上有人追问你和 Marco 的关系。你如何回应？",
        placeholder: "自由输入你的回应……",
        options: [
          { id: "refuse_answer", text: "拒绝回答", desc: "保持距离 · Marco 好感 +1" },
          { id: "explain_him", text: "向他解释", desc: "共同承担 · Marco 好感 +5" },
        ],
      },
    },
    {
      channel: "story",
      msgs: [
        me("这是我们之间的协议，也是我自己的选择。"),
        affin("Marco Diaz 对你的兴趣 +5 ❤️"),
        narr("律所办公室。一张你和 Marco 的照片泄露了。合约关系第一次变成真正的公众压力。"),
        vn("Marco Diaz", "🕴️", "你可以把一切都推给我。或者，告诉他们你并不后悔。"),
      ],
      reply: {
        type: "choice_or_text",
        prompt: "照片泄露后，你要如何回应？",
        placeholder: "自由输入你的公关回应或真实想法……",
        options: [
          { id: "blame_marco", text: "把风波归咎于 Marco", desc: "保守自保 · Marco 好感 +1" },
          { id: "tell_marco_no_regret", text: "告诉 Marco 律所已经知情，而你并不后悔", desc: "大胆坦白 · Marco 好感 +5" },
        ],
      },
    },
    {
      channel: "story",
      msgs: [
        me("我不会后悔。协议是假的，但我的判断不是。"),
        affin("Marco Diaz 对你的兴趣 +5 ❤️"),
        sys("The Clause 已接入文字冒险入口。真实 v1 模式会创建 drama_engine/scripts/interactive_session/story/the_clause.yaml。", { strong: true }),
        narr("合约还在继续。每一次选择都会改变 Marco 的好感度，并最终把 Nora 推向理性、心动或双赢的结局。"),
        narr("—— The Clause · Demo checkpoint ——"),
      ],
    },
  ],
};

const MYSTERY: MockGame = {
  genre: "mystery",
  roles: [
    { seat_id: "me", name: "老赵", emoji: "🎩", role: "管家", controller: "human", description: "真人玩家，持有庄园旧事和遗嘱线索。" },
    { seat_id: "c2", name: "林婉", emoji: "👰", role: "千金", controller: "ai" },
    { seat_id: "c3", name: "陈明", emoji: "🩺", role: "医生", controller: "ai" },
    { seat_id: "c4", name: "秦风", emoji: "🕵️", role: "侦探", controller: "ai" },
    { seat_id: "c5", name: "苏眉", emoji: "💃", role: "歌女", controller: "ai" },
    { seat_id: "c6", name: "赵坤", emoji: "🎓", role: "少爷", controller: "ai" },
  ],
  channels: [
    { id: "table", name: "圆桌", icon: "🍷" },
    { id: "script", name: "我的剧本", icon: "📜", lock: true },
    { id: "dm", name: "私聊·林婉", icon: "🤫", lock: true, badge: 1 },
  ],
  players: [
    { id: "me", name: "老赵(你)", emoji: "🎩", tag: "me", tagText: "管家", online: true },
    { id: "c2", name: "林婉", emoji: "👰", tagText: "千金", online: true },
    { id: "c3", name: "陈明", emoji: "🩺", tagText: "医生", online: true },
    { id: "c4", name: "秦风", emoji: "🕵️", tagText: "侦探", online: true },
    { id: "c5", name: "苏眉", emoji: "💃", tagText: "歌女", online: true },
    { id: "c6", name: "赵坤", emoji: "🎓", tagText: "少爷", online: true },
  ],
  phase: "第 2 轮 · 搜证",
  progress: { label: "搜证轮次", cur: 2, total: 3 },
  steps: [
    {
      channel: "table",
      msgs: [
        sys("🕯️ 欢迎来到《午夜庄园》。庄园主人昨夜离奇身亡，凶手就在你们六人之中。", { strong: true }),
        sys("你选择的角色是：管家·老赵。请先阅读你的私人剧本（见「我的剧本」频道）。"),
      ],
      reply: { type: "confirm", prompt: "阅读剧本后确认", label: "我已阅读剧本 ✓" },
    },
    {
      channel: "table",
      msgs: [sys("第二轮搜证开始。每人可选择一个地点搜索线索。")],
      reply: {
        type: "choice",
        prompt: "选择你要搜证的地点",
        who: "管家·老赵",
        options: [
          { id: "study", text: "📖 书房", desc: "主人生前常待的地方" },
          { id: "cellar", text: "🍷 酒窖", desc: "阴冷潮湿，少有人来" },
          { id: "garden", text: "🌹 后花园", desc: "案发当晚有人在此徘徊" },
          { id: "bedroom", text: "🛏️ 主卧", desc: "案发现场" },
        ],
      },
    },
    {
      channel: "table",
      msgs: [
        sys("你在酒窖的木桶后，发现了一样东西……"),
        clue("半张烧焦的遗嘱", "遗嘱残片上依稀可辨：「……名下全部财产，改由……」后半段已被烧毁。落款日期是案发当天。", "线索 #7 · 仅你可见 · 可选择公开"),
      ],
      reply: {
        type: "choice",
        prompt: "如何处理这条线索？",
        options: [
          { id: "public", text: "📢 公开给全场", desc: "让所有人看到这条线索" },
          { id: "hide", text: "🤐 暂时隐瞒", desc: "留作己用" },
          { id: "share", text: "🤫 私下给林婉", desc: "只分享给指定的人" },
        ],
      },
    },
    {
      channel: "table",
      msgs: [
        me("各位，我在酒窖发现了半张烧焦的遗嘱，主人在案发当天似乎想更改继承人。"),
        chat({ type: "player", id: "c4", name: "秦风", emoji: "🕵️" }, "更改继承人？这可是重大动机。赵坤少爷，你作为第一顺位继承人，怎么解释？"),
        chat({ type: "player", id: "c6", name: "赵坤", emoji: "🎓" }, "凭什么怀疑我！那晚我明明在书房看书，苏眉可以作证！"),
        chat({ type: "player", id: "c5", name: "苏眉", emoji: "💃" }, "我……那晚我确实看见少爷在书房，但只是短短一瞬……"),
        sys("圆桌讨论进行中，请发表你的推理。"),
      ],
      reply: { type: "text", prompt: "作为管家，你掌握着庄园最多的秘密", who: "管家·老赵", placeholder: "说出你的推理，或抛出疑点……" },
    },
    {
      channel: "table",
      msgs: [
        me("赵坤少爷，恕我直言。那晚十点，我巡夜时曾看到书房的灯是熄着的。您说您在书房看书，可当时并没有灯光。"),
        chat({ type: "player", id: "c6", name: "赵坤", emoji: "🎓" }, "你、你胡说！！"),
        chat({ type: "player", id: "c2", name: "林婉", emoji: "👰" }, "老赵说得对，我也记得那晚书房是黑的……哥哥，你到底去了哪里？"),
        sys("🔔 最终指认阶段：请投票选出你认为的凶手。", { strong: true }),
      ],
      reply: {
        type: "vote",
        prompt: "指认凶手 —— 谨慎投票",
        who: "真相只有一个",
        timer: 45,
        candidates: [
          { id: "c2", name: "林婉", emoji: "👰" },
          { id: "c3", name: "陈明", emoji: "🩺" },
          { id: "c4", name: "秦风", emoji: "🕵️" },
          { id: "c5", name: "苏眉", emoji: "💃", count: 1 },
          { id: "c6", name: "赵坤", emoji: "🎓", count: 3 },
        ],
      },
    },
    {
      channel: "table",
      msgs: [
        sys("投票结果：赵坤 以 4 票被指认为凶手。", { strong: true }),
        narr("真相揭晓——赵坤为独吞遗产，在得知父亲要更改遗嘱后痛下杀手。而那半张遗嘱，正是他慌乱中未能烧尽的罪证。"),
        sys("🎉 恭喜！好人阵营成功找出真凶。管家·老赵 获得「关键证人」称号。"),
      ],
    },
  ],
  channelContent: {
    script: [
      secret(
        "📜 你的角色剧本 · 管家 老赵（严格保密）",
        "你在庄园服务了整整三十年，是这里最了解主人的人。\n\n【你的秘密】案发前一周，主人曾私下告诉你，他打算更改遗嘱，剥夺赵坤少爷的继承权。你劝阻过，但主人心意已决。\n\n【你的目标】你深爱这个家族，绝不希望庄园落入凶手之手。找出真凶，还主人一个公道。\n\n【你不知道的】主人更改遗嘱的真正受益人是谁。"
      ),
    ],
    dm: [
      sys("🤫 私聊频道 · 你 与 林婉", { strong: true }),
      chat({ type: "player", id: "c2", name: "林婉", emoji: "👰" }, "老赵……我信得过你。其实那晚我看到哥哥从后花园回来，手上好像沾着什么。"),
      chat({ type: "player", id: "c2", name: "林婉", emoji: "👰" }, "但我不敢当众说，怕被反咬。你有什么发现吗？"),
    ],
  },
};

const VARIETY: MockGame = {
  genre: "variety",
  roles: [
    { seat_id: "me", name: "你", emoji: "😎", role: "嘉宾", controller: "human", description: "真人嘉宾，负责约会邀约和短信选择。" },
    { seat_id: "v2", name: "林夏", emoji: "🌻", role: "女嘉宾", controller: "ai" },
    { seat_id: "v3", name: "周予", emoji: "🎸", role: "男嘉宾", controller: "ai" },
    { seat_id: "v4", name: "小鹿", emoji: "🦌", role: "女嘉宾", controller: "ai" },
    { seat_id: "v5", name: "阿凯", emoji: "🏄", role: "男嘉宾", controller: "ai" },
  ],
  channels: [
    { id: "villa", name: "别墅大厅", icon: "🏝️" },
    { id: "date", name: "约会·小美", icon: "💕", lock: true },
  ],
  players: [
    { id: "me", name: "你", emoji: "😎", tag: "me", online: true },
    { id: "g1", name: "小美", emoji: "👧", online: true },
    { id: "g2", name: "琳琳", emoji: "👩‍🦰", online: true },
    { id: "g3", name: "大壮", emoji: "💪", online: true },
    { id: "g4", name: "阿凯", emoji: "🕺", online: true },
    { id: "g5", name: "婷婷", emoji: "👱‍♀️", online: true, dead: true, tagText: "已离岛" },
  ],
  phase: "第 3 天 · 傍晚",
  progress: { label: "节目进度", cur: 3, total: 7 },
  affinities: [
    { id: "g1", name: "小美→你", emoji: "👧", value: 82, max: 100 },
    { id: "g2", name: "琳琳→你", emoji: "👩‍🦰", value: 54, max: 100 },
    { id: "g3", name: "大壮→你", emoji: "💪", value: 30, max: 100 },
  ],
  circles: [
    { name: "CP 组", emoji: "💑", members: "你 & 小美" },
    { name: "健身圈", emoji: "🏋️", members: "大壮 & 阿凯" },
  ],
  steps: [
    {
      channel: "villa",
      msgs: [sys("🏝️ 恋爱岛 · 第 3 天开始", { strong: true }), sys("📋 今日安排：上午自由交流 → 下午约会 → 傍晚好感互评 → 篝火淘汰")],
      reply: { type: "confirm", prompt: "查看今日安排", label: "了解，开始新的一天 ☀️" },
    },
    {
      channel: "villa",
      msgs: [
        sys("🌞 自由交流时间！和心仪的嘉宾聊聊吧。"),
        chat({ type: "player", id: "g1", name: "小美", emoji: "👧" }, "早安~昨天的约会好开心，我做了早餐，你要一起吃吗？🥐"),
        chat({ type: "player", id: "g3", name: "大壮", emoji: "💪" }, "哟，你俩进展挺快啊。哥们儿我可还单身呢，待会儿游戏可别怪我抢人。"),
      ],
      reply: { type: "text", prompt: "在大厅里发言，所有人都能看到", who: "你 · 现役嘉宾", placeholder: "说点什么活跃气氛……" },
    },
    {
      channel: "villa",
      msgs: [
        me("早安小美，你做的早餐我当然要捧场！大壮你放心，友谊第一比赛第二～"),
        affin("小美 对你的好感 +6 ❤️"),
        chat({ type: "player", id: "g2", name: "琳琳", emoji: "👩‍🦰" }, "哼，就知道围着小美转。（小声）其实……我也做了咖啡的。"),
        sys("💘 约会时间到！你可以邀请一位嘉宾单独约会。"),
      ],
      reply: {
        type: "choice",
        prompt: "选择今天的约会对象（限 1 人）",
        who: "约会将大幅影响好感",
        options: [
          { id: "g1", text: "👧 小美", desc: "好感度 ❤️❤️❤️❤️ 82" },
          { id: "g2", text: "👩‍🦰 琳琳", desc: "好感度 ❤️❤️ 54 · 关系升温中" },
          { id: "g3", text: "💪 大壮", desc: "好感度 ❤️ 30 · 兄弟情" },
        ],
      },
    },
    {
      channel: "villa",
      msgs: [
        me("小美，今天……可以和我单独去海边走走吗？"),
        affin("小美 对你的好感 +10 ❤️❤️"),
        narr("你和小美的约会开始了 —— 地点：黄昏下的海边沙滩 🌅（详见「约会·小美」频道）"),
        sys("约会结束，回到别墅。🌆 傍晚好感互评开始，请为每位嘉宾打分。"),
      ],
      reply: {
        type: "form",
        prompt: "为每位嘉宾打分（1–10）",
        who: "评分将决定今晚的去留",
        fields: [
          { id: "g1", label: "👧 小美", type: "range", min: 1, max: 10, value: 9 },
          { id: "g2", label: "👩‍🦰 琳琳", type: "range", min: 1, max: 10, value: 6 },
          { id: "g3", label: "💪 大壮", type: "range", min: 1, max: 10, value: 7 },
        ],
      },
    },
    {
      channel: "villa",
      msgs: [
        sys("📊 评分提交成功。综合全场好感，今晚的篝火淘汰名单已生成。"),
        sys("🔥 篝火晚会 · 请投票选出你认为最应该离岛的嘉宾。", { strong: true }),
      ],
      reply: {
        type: "vote",
        prompt: "篝火淘汰投票",
        who: "残酷但真实",
        timer: 30,
        candidates: [
          { id: "g2", name: "琳琳", emoji: "👩‍🦰" },
          { id: "g3", name: "大壮", emoji: "💪", count: 2 },
          { id: "g4", name: "阿凯", emoji: "🕺", count: 1 },
        ],
      },
    },
    {
      channel: "villa",
      msgs: [
        sys("🔥 今晚离岛的是：大壮。他与大家挥手告别。", { strong: true }),
        affin("你与小美的 CP 值已达 82，稳居全场第一 👑"),
        sys("第 3 天结束。明天，海岛上又会发生怎样的故事？"),
      ],
    },
  ],
  channelContent: {
    date: [
      sys("💕 约会频道 · 你 与 小美 · 海边沙滩", { strong: true }),
      vn("小美", "👧", "哇……夕阳好美啊。谢谢你今天选择了我，其实……我一直在等你开口呢。"),
      chat({ type: "player", id: "me", name: "你", emoji: "😎" }, "那以后每天的夕阳，都想和你一起看。", { self: true }),
    ],
  },
};

const BOARD: MockGame = {
  genre: "board",
  roles: [
    { seat_id: "black", name: "黑棋", emoji: "●", role: "先手", controller: "human", description: "真人玩家，执黑先行。" },
    { seat_id: "white", name: "白棋 AI", emoji: "○", role: "后手", controller: "ai", description: "AI 对手，负责白棋落子。" },
  ],
  channels: [{ id: "board", name: "棋盘", icon: "⚫" }],
  players: [
    { id: "me", name: "你", emoji: "😎", tag: "me", tagText: "黑棋 ●", online: true },
    { id: "ai", name: "AI 棋手", emoji: "🤖", tagText: "白棋 ○", online: true },
  ],
  phase: "对局中 · 第 5 手",
  boardState: [".........", ".... ●...", "....○●...", ".... ○●..", "....○ ...", ".........", ".........", ".........", "........."],
  steps: [
    {
      channel: "board",
      msgs: [sys("⚫ 五子棋对局开始，你执黑先行。先连成 5 子者获胜。", { strong: true }), sys("落子由服务端裁定，防止作弊。请输入坐标。")],
      reply: {
        type: "structured",
        prompt: "轮到你落子（黑棋 ●）",
        who: "输入棋盘坐标",
        fields: [
          { id: "row", label: "行 (0–8)", type: "number", placeholder: "7" },
          { id: "col", label: "列 (0–8)", type: "number", placeholder: "7" },
        ],
      },
    },
    {
      channel: "board",
      msgs: [
        me("落子 → (行 4, 列 5)"),
        sys("你落子于 (4,5)。黑棋已形成三连，威胁成型。"),
        chat({ type: "player", id: "ai", name: "AI 棋手", emoji: "🤖" }, "白棋落子 (4,6)，堵住你的活三。该你了。"),
      ],
      reply: {
        type: "structured",
        prompt: "继续落子（黑棋 ●）",
        who: "AI 已封堵一端，寻找新战机",
        fields: [
          { id: "row", label: "行 (0–8)", type: "number", placeholder: "3" },
          { id: "col", label: "列 (0–8)", type: "number", placeholder: "5" },
        ],
      },
    },
    {
      channel: "board",
      msgs: [
        me("落子 → (行 3, 列 5)"),
        sys("你落子于 (3,5)，形成双活三！AI 无法同时封堵。", { strong: true }),
        chat({ type: "player", id: "ai", name: "AI 棋手", emoji: "🤖" }, "……这一手很妙。我只能堵一边了。"),
        sys("🏆 你在下一手连成五子，黑棋胜利！", { strong: true }),
        narr("好棋。再来一局？"),
      ],
    },
  ],
};

export const SCRIPTS: Record<string, MockGame> = { werewolf: WEREWOLF, the_clause: THE_CLAUSE, mystery: MYSTERY, variety: VARIETY, board: BOARD };
