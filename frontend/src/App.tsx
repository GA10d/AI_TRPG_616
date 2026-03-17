import { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import parchmentBg from "../羊皮纸叙事.png";
import nightwatchBg from "../夜航控制台.png";
import neonBg from "../赛博霓虹.png";
import {
  CatalogResponse,
  RuleOption,
  SessionResponse,
  StoryOption,
  deleteSession,
  fetchCatalog,
  streamCreateSession,
  streamTurn,
} from "./trpg/trpg_client";

type MessageRole = "system" | "player" | "ai";
type ThemeName = "parchment" | "nightwatch" | "neon";
type ThemeBackground = Record<ThemeName, string>;

interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: number;
}

const themeBackgrounds: ThemeBackground = {
  parchment: parchmentBg,
  nightwatch: nightwatchBg,
  neon: neonBg,
};

function createMessage(role: MessageRole, content: string): ChatMessage {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return { id, role, content, createdAt: Date.now() };
}

function updateMessageContent(messages: ChatMessage[], id: string, content: string): ChatMessage[] {
  return messages.map((message) => (message.id === id ? { ...message, content } : message));
}

function hasMessageContent(messages: ChatMessage[], content: string): boolean {
  return messages.some((message) => message.content === content);
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function renderInlineRichText(text: string): ReactNode[] {
  const parts = text.split(/(\*\*.*?\*\*)/g);
  return parts.filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
      return <strong key={`strong-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return <span key={`span-${index}`}>{part}</span>;
  });
}

function renderRichText(text: string): ReactNode {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];
  let paragraphLines: string[] = [];

  const flushList = () => {
    if (listItems.length === 0) return;
    blocks.push(
      <ul key={`list-${blocks.length}`} className="rich-text-list">
        {listItems.map((item, index) => (
          <li key={`item-${index}`}>{renderInlineRichText(item)}</li>
        ))}
      </ul>
    );
    listItems = [];
  };

  const flushParagraph = () => {
    if (paragraphLines.length === 0) return;
    const content = paragraphLines.join(" ").trim();
    if (!content) {
      paragraphLines = [];
      return;
    }
    blocks.push(
      <p key={`p-${blocks.length}`} className="rich-text-paragraph">
        {renderInlineRichText(content)}
      </p>
    );
    paragraphLines = [];
  };

  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      flushParagraph();
      return;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      flushList();
      flushParagraph();
      const level = Math.min(headingMatch[1].length, 3);
      const title = headingMatch[2].trim();
      const Tag = level === 1 ? "h2" : level === 2 ? "h3" : "h4";
      blocks.push(
        <Tag key={`h-${blocks.length}`} className={`rich-text-heading level-${level}`}>
          {renderInlineRichText(title)}
        </Tag>
      );
      return;
    }

    const listMatch = line.match(/^([-*]|\d+[.)])\s+(.*)$/);
    if (listMatch) {
      flushParagraph();
      listItems.push(listMatch[2].trim());
      return;
    }

    flushList();
    paragraphLines.push(line);
  });

  flushList();
  flushParagraph();

  if (blocks.length === 0) {
    return <p className="rich-text-paragraph">{text}</p>;
  }
  return <div className="rich-text">{blocks}</div>;
}

function toJoinedValue(values: string[], fallback = "暂无"): string {
  return values.length > 0 ? values.join("、") : fallback;
}

function buildStatus(session: SessionResponse | null): Array<{ key: string; value: string }> {
  if (!session) {
    return [
      { key: "规则", value: "未开始" },
      { key: "剧本", value: "未开始" },
      { key: "场景", value: "等待创建会话" },
      { key: "剩余轮次", value: "-" },
    ];
  }

  const { state } = session;
  return [
    { key: "规则", value: session.rule_code },
    { key: "剧本", value: state.scenario.title || session.story_code },
    { key: "场景", value: state.scene.location || "未知" },
    { key: "剩余轮次", value: `${session.turns_remaining}/${session.max_turns}` },
    { key: "可见 NPC", value: toJoinedValue(state.scene.visible_npcs) },
    { key: "危险", value: toJoinedValue(state.scene.hazards) },
    { key: "物品", value: toJoinedValue(state.player.inventory) },
    { key: "线索", value: toJoinedValue(state.player.known_clues) },
  ];
}

export default function App() {
  const [theme, setTheme] = useState<ThemeName>(() => {
    const cached = localStorage.getItem("trpg-theme");
    if (cached === "parchment" || cached === "nightwatch" || cached === "neon") return cached;
    return "parchment";
  });
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [playerName, setPlayerName] = useState("玩家");
  const [selectedRule, setSelectedRule] = useState("");
  const [selectedStory, setSelectedStory] = useState("");
  const [maxTurns, setMaxTurns] = useState(12);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [startingSession, setStartingSession] = useState(false);
  const [submittingTurn, setSubmittingTurn] = useState(false);
  const [operationError, setOperationError] = useState("");

  const messageListRef = useRef<HTMLElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const themeLabels: Record<ThemeName, string> = {
    parchment: "羊皮纸叙事",
    nightwatch: "夜航控制台",
    neon: "赛博霓虹",
  };

  const selectedRuleEntry = useMemo<RuleOption | undefined>(
    () => catalog?.rules.find((rule) => rule.rule_code === selectedRule),
    [catalog, selectedRule]
  );

  const stories = selectedRuleEntry?.stories ?? [];
  const selectedStoryEntry = useMemo<StoryOption | undefined>(
    () => stories.find((story) => story.story_code === selectedStory),
    [selectedStory, stories]
  );

  const filteredMessages = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return messages;
    return messages.filter((message) => message.content.toLowerCase().includes(keyword));
  }, [messages, search]);

  const canStartSession = Boolean(selectedRule && selectedStory) && !startingSession && !submittingTurn;
  const canSend = input.trim().length > 0 && !startingSession && !submittingTurn && Boolean(session) && !session.is_finished;
  const status = useMemo(() => buildStatus(session), [session]);

  useEffect(() => {
    localStorage.setItem("trpg-theme", theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    setLoadingCatalog(true);
    fetchCatalog()
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        setCatalogError("");
        const firstRule = data.rules[0];
        if (!firstRule) return;
        setSelectedRule((current) => current || firstRule.rule_code);
        setSelectedStory((current) => current || firstRule.stories[0]?.story_code || "");
      })
      .catch((error) => {
        if (cancelled) return;
        setCatalogError(error instanceof Error ? error.message : "无法加载规则与剧本列表");
      })
      .finally(() => {
        if (!cancelled) setLoadingCatalog(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedRuleEntry) return;
    if (!stories.some((story) => story.story_code === selectedStory)) {
      setSelectedStory(stories[0]?.story_code ?? "");
    }
  }, [selectedRuleEntry, selectedStory, stories]);

  useEffect(() => {
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: "smooth" });
  }, [filteredMessages, submittingTurn, startingSession]);

  useEffect(() => {
    return () => {
      const sessionId = sessionIdRef.current;
      if (!sessionId) return;
      void deleteSession(sessionId).catch(() => undefined);
    };
  }, []);

  const handleStartSession = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canStartSession) return;

    setStartingSession(true);
    setOperationError("");
    try {
      const oldSessionId = sessionIdRef.current;
      if (oldSessionId) {
        await deleteSession(oldSessionId).catch(() => undefined);
      }

      setMessages([createMessage("system", "正在创建会话并生成开场，请稍候...")]);

      let nextSession: SessionResponse | null = null;
      await streamCreateSession(
        {
          rule_code: selectedRule,
          story_code: selectedStory,
          player_name: playerName.trim() || "玩家",
          max_turns: maxTurns,
        },
        (streamEvent) => {
          if (streamEvent.event === "runtime_log") {
            setMessages((current) =>
              hasMessageContent(current, streamEvent.message)
                ? current
                : [...current, createMessage("system", streamEvent.message)]
            );
            return;
          }

          if (streamEvent.event === "session_ready") {
            nextSession = streamEvent.session;
            sessionIdRef.current = streamEvent.session.session_id;
            setSession(streamEvent.session);
            setMessages((current) => [
              ...current,
              createMessage(
                "system",
                `会话已创建：${streamEvent.session.rule_code} / ${
                  streamEvent.session.state.scenario.title || streamEvent.session.story_code
                }`
              ),
              createMessage("ai", streamEvent.session.opening),
            ]);
            return;
          }

          if (streamEvent.event === "error") {
            throw new Error(streamEvent.error);
          }
        }
      );

      if (nextSession === null) {
        throw new Error("会话创建流未返回最终结果");
      }
      setInput("");
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "创建会话失败");
    } finally {
      setStartingSession(false);
    }
  };

  const sendMessage = async (rawText: string) => {
    const trimmed = rawText.trim();
    const activeSession = session;
    if (!trimmed || !activeSession || submittingTurn || activeSession.is_finished) return;

    setSubmittingTurn(true);
    setOperationError("");
    const playerMessage = createMessage("player", trimmed);
    const aiMessage = createMessage("ai", "");
    setMessages((current) => [...current, playerMessage, aiMessage]);

    try {
      let finalSession: SessionResponse | null = null;
      let finalNarration = "";
      let turnStartLogged = false;

      await streamTurn(activeSession.session_id, trimmed, (event) => {
        if (event.event === "turn_start") {
          if (!turnStartLogged) {
            turnStartLogged = true;
            setMessages((current) => [...current, createMessage("system", "正在推进回合，主持人开始组织叙事...")]);
          }
          return;
        }

        if (event.event === "narration_chunk") {
          finalNarration += event.delta;
          setMessages((current) => updateMessageContent(current, aiMessage.id, finalNarration));
          return;
        }

        if (event.event === "turn_result") {
          finalSession = event.session;
          finalNarration = event.turn.narration;
          sessionIdRef.current = event.session.session_id;
          setSession(event.session);
          setMessages((current) => updateMessageContent(current, aiMessage.id, finalNarration));
          if (event.session.is_finished) {
            setMessages((current) => [...current, createMessage("system", "已达到本局最大对话轮次，请重新创建会话继续体验。")]);
          }
          return;
        }

        if (event.event === "error") {
          throw new Error(event.error);
        }
      });

      if (finalSession === null) {
        throw new Error("流式回合未返回最终结果");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "回合推进失败";
      setOperationError(message);
      setMessages((current) => [
        ...updateMessageContent(current, aiMessage.id, ""),
        createMessage("system", `本次行动提交失败：${message}`),
      ]);
    } finally {
      setSubmittingTurn(false);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSend) return;
    const current = input;
    setInput("");
    void sendMessage(current);
  };

  const onInputKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!canSend) return;
      const current = input;
      setInput("");
      void sendMessage(current);
    }
  };

  const storySummary = session?.opening || selectedStoryEntry?.opening_scene || "选择规则和剧本后，点击“开始会话”生成真实开场。";

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
            <h1>Direct Play 控制台</h1>
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
              <h2>开场摘要</h2>
              <div className="story-rich-text">{renderRichText(storySummary)}</div>
            </section>

            <section className="status-card">
              <h3>玩家配置</h3>
              <form className="session-form" onSubmit={handleStartSession}>
                <label>
                  玩家名
                  <input value={playerName} onChange={(event) => setPlayerName(event.target.value)} maxLength={24} />
                </label>

                <label>
                  规则
                  <select
                    value={selectedRule}
                    onChange={(event) => setSelectedRule(event.target.value)}
                    disabled={loadingCatalog || startingSession}
                  >
                    {catalog?.rules.map((rule) => (
                      <option key={rule.rule_code} value={rule.rule_code}>
                        {rule.rule_code}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  剧本
                  <select
                    value={selectedStory}
                    onChange={(event) => setSelectedStory(event.target.value)}
                    disabled={loadingCatalog || startingSession || stories.length === 0}
                  >
                    {stories.map((story) => (
                      <option key={story.story_code} value={story.story_code}>
                        {story.title || story.story_code}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  最大对话轮次
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={maxTurns}
                    onChange={(event) => setMaxTurns(Number(event.target.value) || 1)}
                  />
                </label>

                <div className="session-form-actions">
                  <button type="submit" disabled={!canStartSession}>
                    {startingSession ? "创建中..." : session ? "重新开始" : "开始会话"}
                  </button>
                </div>
              </form>
              {loadingCatalog ? <p className="form-note">正在读取可用规则与剧本...</p> : null}
              {catalogError ? <p className="form-note error">列表加载失败：{catalogError}</p> : null}
              {operationError ? <p className="form-note error">操作失败：{operationError}</p> : null}
            </section>

            <section className="status-card">
              <h3>当前状态</h3>
              <ul>
                {status.map((item) => (
                  <li key={item.key}>
                    <span>{item.key}</span>
                    <strong>{item.value}</strong>
                  </li>
                ))}
              </ul>
            </section>

            <section className="status-card">
              <h3>场景详情</h3>
              <ul>
                <li>
                  <span>时间</span>
                  <strong>
                    {session
                      ? `第 ${session.state.game_time.day} 天 ${String(session.state.game_time.hour).padStart(2, "0")}:${String(
                          session.state.game_time.minute
                        ).padStart(2, "0")}`
                      : "-"}
                  </strong>
                </li>
                <li>
                  <span>场景描述</span>
                  <strong>{session?.state.scene.description || "等待开场"}</strong>
                </li>
                <li>
                  <span>可交互</span>
                  <strong>{session ? toJoinedValue(session.state.scene.interactive_objects) : "-"}</strong>
                </li>
                <li>
                  <span>近期事件</span>
                  <strong>{session ? toJoinedValue(session.state.recent_events) : "-"}</strong>
                </li>
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
              {filteredMessages.length === 0 ? (
                <article className="chat-bubble system">
                  <header>
                    <strong>系统提示</strong>
                    <span>待机</span>
                  </header>
                  <div className="chat-bubble-body">
                    <p>先在左侧选择规则、剧本和最大对话轮次，然后开始会话。</p>
                  </div>
                </article>
              ) : null}

              {filteredMessages.map((message) => (
                <article key={message.id} className={`chat-bubble ${message.role}`}>
                  <header>
                    <strong>{message.role === "player" ? "玩家" : message.role === "ai" ? "主持人" : "系统"}</strong>
                    <span>{formatTime(message.createdAt)}</span>
                  </header>
                  <div className="chat-bubble-body">{renderRichText(message.content)}</div>
                </article>
              ))}

              {(startingSession || submittingTurn) && (
                <article className="chat-bubble ai typing">
                  <header>
                    <strong>主持人</strong>
                    <span>处理中</span>
                  </header>
                  <div className="chat-bubble-body">
                    <p>{startingSession ? "正在生成开场叙事..." : "正在推进当前行动..."}</p>
                  </div>
                </article>
              )}
            </section>

            <form onSubmit={handleSubmit} className="composer">
              <textarea
                id="player-input"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onInputKeyDown}
                placeholder="行动示例：进入大厅观察壁画、询问守卫昨晚发生了什么"
                rows={4}
                disabled={!session || session.is_finished || startingSession}
              />
              <div className="composer-footer">
                <span>{session ? `当前 ${session.turns_used}/${session.max_turns} 轮` : "请先开始会话"}</span>
                <div className="composer-actions">
                  <button type="button" onClick={() => setInput("")}>
                    清空
                  </button>
                  <button type="submit" disabled={!canSend}>
                    {submittingTurn ? "发送中..." : "发送"}
                  </button>
                </div>
              </div>
            </form>
          </section>
        </section>
      </main>
    </div>
  );
}
