import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import "./App.css";
import parchmentBg from "../羊皮纸叙事.png";
import nightwatchBg from "../夜航控制台.png";
import neonBg from "../赛博霓虹.png";

type MessageRole = "system" | "player" | "ai";
type ThemeName = "parchment" | "nightwatch" | "neon";
type ThemeBackground = Record<ThemeName, string>;

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
  "游戏规则简介",
  "本场游戏采用《边界线》写实冒险角色扮演规则，以贴近现实的细节叙事为核心。",
  "背景故事",
  "你追踪盗掘者车辙深入沙漠，抵达废弃遗迹『黑砂古城』。",
  "目标",
  "1. 探索真相，抵达遗迹核心。",
  "2. 生存撤离，在资源受限与多重威胁下找到安全路径。"
].join("\n\n");

const initialState: ChatState = {
  messages: [
    {
      id: "seed-0",
      role: "system",
      content: "场景载入完成：风暴将在 3 小时后抵达黑砂古城。",
      createdAt: Date.now() - 60_000,
    },
    {
      id: "seed-1",
      role: "ai",
      content: "欢迎。你站在黑砂古城入口，风暴正在逼近。你准备先做什么？",
      createdAt: Date.now(),
    },
  ],
  isAiTyping: false,
};

const themeBackgrounds: ThemeBackground = {
  parchment: parchmentBg,
  nightwatch: nightwatchBg,
  neon: neonBg,
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
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
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
    { key: "对怪物认知", value: "未知" },
  ]);

  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const messageListRef = useRef<HTMLElement | null>(null);

  const themeLabels: Record<ThemeName, string> = {
    parchment: "羊皮纸叙事",
    nightwatch: "夜航控制台",
    neon: "赛博霓虹",
  };

  const canSend = useMemo(() => input.trim().length > 0 && !state.isAiTyping, [input, state.isAiTyping]);

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

  const onInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (canSend) {
          sendMessage(input);
          setInput("");
        }
      }
    },
    [canSend, input, sendMessage]
  );

  useEffect(() => {
    localStorage.setItem("trpg-theme", theme);
  }, [theme]);

  useEffect(() => {
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: "smooth" });
  }, [state.messages, state.isAiTyping]);

  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, []);

  return (
    <div className="app-root" data-theme={theme}>
      <div className="theme-background-layer" aria-hidden="true">
        <img src={themeBackgrounds[theme]} alt="" />
      </div>
      <div className="theme-background-overlay" aria-hidden="true" />

      <main className="app-shell">
        <header className="app-header app-card">
          <div>
            <p className="eyebrow">AI TRPG MANAGER</p>
            <h1>黑砂古城行动面板</h1>
          </div>
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

        <section className="app-body">
          <aside className="sidebar app-card">
            <section className="story-card">
              <h2>任务摘要</h2>
              <pre>{openingStory}</pre>
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

          <section className="chat app-card">
            <header className="chat-toolbar">
              <strong>会话记录</strong>
              <label>
                搜索
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="关键词过滤" />
              </label>
            </header>

            <section className="message-list" ref={messageListRef}>
              {filteredHistory.map((message) => (
                <article key={message.id} className={`chat-bubble ${message.role}`}>
                  <header>
                    <strong>{message.role === "player" ? "玩家" : message.role === "ai" ? "主持人" : "系统"}</strong>
                    <span>{formatTime(message.createdAt)}</span>
                  </header>
                  <p>{message.content}</p>
                </article>
              ))}

              {state.isAiTyping && (
                <article className="chat-bubble ai typing">
                  <header>
                    <strong>主持人</strong>
                    <span>输入中</span>
                  </header>
                  <p>主持人正在思考下一步剧情...</p>
                </article>
              )}
            </section>

            <form onSubmit={handleSubmit} className="composer">
              <textarea
                id="player-input"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onInputKeyDown}
                placeholder="输入你的行动，例如：先观察入口左侧墙面的刻痕。"
                rows={4}
              />
              <div className="composer-footer">
                <span>Enter 发送 | Shift+Enter 换行</span>
                <div className="composer-actions">
                  <button type="button" onClick={() => setInput("")}>清空</button>
                  <button type="submit" disabled={!canSend}>发送</button>
                </div>
              </div>
            </form>
          </section>
        </section>
      </main>
    </div>
  );
}
