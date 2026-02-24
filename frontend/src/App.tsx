import { FormEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import "./App.css";

type MessageRole = "player" | "ai";
type ThemeName = "parchment" | "nightwatch" | "neon";

interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: number;
}

interface ChatState {
  messages: ChatMessage[];
  isAiTyping: boolean;
}

type ChatAction =
  | { type: "append"; payload: ChatMessage }
  | { type: "setTyping"; payload: boolean };

const openingStory = [
  "主持人：",
  "",
  "游戏规则简介",
  "本场游戏采用《边界线》写实冒险角色扮演规则，以贴近现实的细节叙事为核心，通过玩家的探索、行动与有限超能力动态推进剧情。",
  "",
  "背景故事概述",
  "【背景故事】现代，你追踪着一伙盗掘者的车辙深入沙漠，抵达了传说中的废弃遗迹「黑砂古城」。",
  "",
  "游戏目的",
  "1. 探索真相：在沙暴掩埋一切前，深入遗迹核心。",
  "2. 生存撤离：在有限资源与多重威胁中找到安全路径。"
].join("\n");

const initialState: ChatState = {
  messages: [
    {
      id: "seed-1",
      role: "ai",
      content: "欢迎。你站在黑砂古城的入口，风暴正在逼近。你准备先做什么？",
      createdAt: Date.now(),
    },
  ],
  isAiTyping: false,
};

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "append":
      return { ...state, messages: [...state.messages, action.payload] };
    case "setTyping":
      return { ...state, isAiTyping: action.payload };
    default:
      return state;
  }
}

function createMessage(role: MessageRole, content: string): ChatMessage {
  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random()}`;

  return { id, role, content, createdAt: Date.now() };
}

function buildAiReply(playerInput: string): string {
  return `你尝试“${playerInput}”。地面传来轻微震动，远处有金属反光。你要继续靠近，还是先观察周边地形？`;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export default function App() {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [theme, setTheme] = useState<ThemeName>(() => {
    const cached = localStorage.getItem("trpg-theme");
    if (cached === "parchment" || cached === "nightwatch" || cached === "neon") return cached;
    return "parchment";
  });
  const [status] = useState([
    { key: "生理状态", value: "良好" },
    { key: "恐惧程度", value: "低" },
    { key: "NPC队友", value: "暂无" },
    { key: "背包物品", value: "手电、绷带、半壶水" },
    { key: "对怪物认知", value: "未知" }
  ]);

  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const rightPaneRef = useRef<HTMLElement | null>(null);

  const themeLabels: Record<ThemeName, string> = {
    parchment: "羊皮纸叙事",
    nightwatch: "夜航控制台",
    neon: "霓虹赛博"
  };

  const canSend = useMemo(() => input.trim().length > 0 && !state.isAiTyping, [input, state.isAiTyping]);
  const narratorText = useMemo(() => {
    const aiLines = state.messages
      .filter((m) => m.role === "ai")
      .map((m) => `[${formatTime(m.createdAt)}] ${m.content}`);
    return [openingStory, "", "主持人发言记录", ...aiLines].join("\n\n");
  }, [state.messages]);
  const filteredHistory = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return state.messages;
    return state.messages.filter((m) => m.content.toLowerCase().includes(keyword));
  }, [search, state.messages]);

  const sendMessage = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    dispatch({ type: "append", payload: createMessage("player", trimmed) });
    dispatch({ type: "setTyping", payload: true });

    const timer = setTimeout(() => {
      dispatch({ type: "append", payload: createMessage("ai", buildAiReply(trimmed)) });
      dispatch({ type: "setTyping", payload: false });
    }, 1000);

    timersRef.current.push(timer);
  }, []);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      sendMessage(input);
      setInput("");
    },
    [input, sendMessage]
  );

  useEffect(() => {
    rightPaneRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [state.messages, state.isAiTyping]);

  useEffect(() => {
    localStorage.setItem("trpg-theme", theme);
  }, [theme]);

  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, []);

  return (
    <main className={`manager-shell theme-${theme}`}>
      <header className="window-title">
        <span>AI TRPG Manager</span>
        <div className="theme-switcher" role="tablist" aria-label="主题切换">
          {(Object.keys(themeLabels) as ThemeName[]).map((themeName) => (
            <button
              key={themeName}
              type="button"
              className={theme === themeName ? "active" : ""}
              onClick={() => setTheme(themeName)}
              aria-pressed={theme === themeName}
            >
              {themeLabels[themeName]}
            </button>
          ))}
        </div>
      </header>

      <section className="top-input">
        <label htmlFor="player-input">玩家输入：</label>
        <form onSubmit={handleSubmit} className="input-block">
          <textarea
            id="player-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="玩家在这里输入..."
            rows={4}
          />
          <div className="input-actions">
            <span>Enter 发送 | Shift+Enter 换行 | Ctrl+L 清空输入</span>
            <button type="submit" disabled={!canSend}>
              发送
            </button>
          </div>
        </form>
      </section>

      <section className="content-grid">
        <article className="left-panel">
          <h2>主持人</h2>
          <section className="story-scroll" aria-label="主持人发言内容">
            <pre className="story">{narratorText}</pre>
          </section>
          {state.isAiTyping && <p className="typing">主持人正在思考...</p>}
        </article>

        <aside className="right-panel">
          <div className="history-head">
            <strong>历史记录</strong>
            <label>
              搜索
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入关键词过滤" />
            </label>
          </div>

          <section className="history-list" ref={rightPaneRef}>
            {filteredHistory.map((m) => (
              <article key={m.id} className="history-item">
                <div>
                  <strong>{m.role === "player" ? "玩家" : "主持人"}</strong>
                  <span>{formatTime(m.createdAt)}</span>
                </div>
                <p>{m.content}</p>
              </article>
            ))}
          </section>

          <section className="status-card">
            <h3>玩家状态</h3>
            <ul>
              {status.map((item) => (
                <li key={item.key}>
                  <span>{item.key}</span>
                  <strong>{item.value}</strong>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </section>

      <footer className="bottom-actions">
        <div className="left-buttons">
          <button type="button">读档</button>
          <button type="button">存档</button>
          <button type="button">导出回放</button>
        </div>
        <div className="right-buttons">
          <button type="button">下一步</button>
          <button type="button">重试</button>
          <button type="button">停止</button>
        </div>
      </footer>
    </main>
  );
}
