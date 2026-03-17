import { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import parchmentBg from "../羊皮纸叙事.png";
import nightwatchBg from "../夜航控制台.png";
import neonBg from "../赛博霓虹.png";
import {
  CatalogResponse,
  RuleOption,
  SaveEntry,
  SessionResponse,
  StoryOption,
  StreamTurnAgentEvent,
  TranscriptEntry,
  deleteSession,
  exportHistory,
  fetchCatalog,
  listSaves,
  loadSession,
  saveSession,
  streamCreateSession,
  streamTurn,
} from "./trpg/trpg_client";

type MessageRole = "system" | "player" | "ai";
type ThemeName = "parchment" | "nightwatch" | "neon";

interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: number;
}

interface AgentMonitorState {
  dicer: string;
  npcManager: string;
  directorState: string;
  director: string;
  narrator: string;
}

interface PanelSectionProps {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

const themeBackgrounds: Record<ThemeName, string> = {
  parchment: parchmentBg,
  nightwatch: nightwatchBg,
  neon: neonBg,
};

const themeLabels: Record<ThemeName, string> = {
  parchment: "羊皮纸叙事",
  nightwatch: "夜航控制台",
  neon: "赛博霓虹",
};

const emptyAgentMonitor: AgentMonitorState = {
  dicer: "等待回合开始",
  npcManager: "等待回合开始",
  directorState: "等待会话开始",
  director: "等待本回合结束",
  narrator: "等待旁白生成",
};

function createMessage(role: MessageRole, content: string, createdAt = Date.now()): ChatMessage {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${createdAt}-${Math.random()}`;
  return { id, role, content, createdAt };
}

function transcriptToMessages(transcript: TranscriptEntry[]): ChatMessage[] {
  return transcript
    .filter((entry) => entry.role === "system" || entry.role === "player" || entry.role === "ai")
    .map((entry, index) =>
      createMessage(
        entry.role,
        entry.content,
        typeof entry.created_at === "number" ? entry.created_at : Date.now() + index
      )
    );
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
    paragraphLines = [];
    if (!content) return;
    blocks.push(
      <p key={`paragraph-${blocks.length}`} className="rich-text-paragraph">
        {renderInlineRichText(content)}
      </p>
    );
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
        <Tag key={`heading-${blocks.length}`} className={`rich-text-heading level-${level}`}>
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

  return blocks.length > 0 ? <div className="rich-text">{blocks}</div> : <p className="rich-text-paragraph">{text}</p>;
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

function formatJsonBlock(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function summarizeAgentEvent(event: StreamTurnAgentEvent): string {
  const payload = event.payload;
  if (event.agent_name === "dicer") {
    return formatJsonBlock({
      validity: payload.validity,
      resolution: payload.resolution,
      event_log_entries: payload.event_log_entries,
      state_delta: payload.state_delta,
    });
  }
  if (event.agent_name === "npc_manager") {
    return formatJsonBlock({
      visible_npcs_output: payload.visible_npcs_output,
      background_updates: payload.background_updates,
      event_log_entries: payload.event_log_entries,
      state_delta: payload.state_delta,
    });
  }
  if (event.agent_name === "director_state") {
    return formatJsonBlock(payload);
  }
  if (event.agent_name === "director") {
    return formatJsonBlock({
      guidance: payload.guidance,
      triggered_events: payload.triggered_events,
      event_log_entries: payload.event_log_entries,
      state_delta: payload.state_delta,
    });
  }
  return formatJsonBlock(payload);
}

function PanelSection({ title, open, onToggle, children }: PanelSectionProps) {
  return (
    <section className="panel-card">
      <button type="button" className="panel-toggle" onClick={onToggle} aria-expanded={open}>
        <span>{title}</span>
        <strong>{open ? "收起" : "展开"}</strong>
      </button>
      {open ? <div className="panel-body">{children}</div> : null}
    </section>
  );
}

function truncateStoryTitle(story: StoryOption): string {
  const source = story.title?.trim() || story.story_code;
  return source.length > 42 ? `${source.slice(0, 42)}...` : source;
}

export default function App() {
  const [theme, setTheme] = useState<ThemeName>(() => {
    const cached = localStorage.getItem("trpg-theme");
    return cached === "parchment" || cached === "nightwatch" || cached === "neon" ? cached : "parchment";
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
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(true);
  const [sceneOpen, setSceneOpen] = useState(false);
  const [saveEntries, setSaveEntries] = useState<SaveEntry[]>([]);
  const [selectedSaveFile, setSelectedSaveFile] = useState("");
  const [agentMonitor, setAgentMonitor] = useState<AgentMonitorState>(emptyAgentMonitor);

  const messageListRef = useRef<HTMLElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);

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

  const refreshSaves = async () => {
    try {
      const response = await listSaves();
      setSaveEntries(response.saves);
      setSelectedSaveFile((current) => current || response.saves[0]?.file_name || "");
    } catch {
      // Ignore save refresh failure in UI and keep play usable.
    }
  };

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
    void refreshSaves();
  }, []);

  useEffect(() => {
    if (!selectedRuleEntry) return;
    if (!stories.some((story) => story.story_code === selectedStory)) {
      setSelectedStory(stories[0]?.story_code ?? "");
    }
  }, [selectedRuleEntry, selectedStory, stories]);

  useEffect(() => {
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: "smooth" });
  }, [filteredMessages, startingSession, submittingTurn]);

  useEffect(() => {
    return () => {
      const sessionId = sessionIdRef.current;
      if (!sessionId) return;
      void deleteSession(sessionId).catch(() => undefined);
    };
  }, []);

  const resetForNewSession = async () => {
    const oldSessionId = sessionIdRef.current;
    if (oldSessionId) {
      await deleteSession(oldSessionId).catch(() => undefined);
    }
    sessionIdRef.current = null;
    setSession(null);
  };

  const handleStartSession = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canStartSession) return;

    setStartingSession(true);
    setOperationError("");
    setAgentMonitor(emptyAgentMonitor);
    setSummaryOpen(false);

    try {
      await resetForNewSession();
      setMessages([createMessage("system", "正在创建会话并生成开场，请稍候...")]);

      let readySession: SessionResponse | null = null;
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
              hasMessageContent(current, streamEvent.message) ? current : [...current, createMessage("system", streamEvent.message)]
            );
            return;
          }

          if (streamEvent.event === "session_ready") {
            readySession = streamEvent.session;
            sessionIdRef.current = streamEvent.session.session_id;
            setSession(streamEvent.session);
            setMessages((current) => {
              const next = [...current, createMessage("system", `会话已创建：${streamEvent.session.state.scenario.title || streamEvent.session.story_code}`)];
              const history = transcriptToMessages(streamEvent.session.transcript);
              return [...next, ...history];
            });
            return;
          }

          throw new Error(streamEvent.error);
        }
      );

      if (!readySession) {
        throw new Error("会话创建未返回最终结果");
      }

      setInput("");
      await refreshSaves();
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
    setAgentMonitor((current) => ({
      ...current,
      dicer: "正在判定...",
      npcManager: "正在处理 NPC 反应...",
      director: "等待本回合结束...",
      narrator: "",
    }));

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

        if (event.event === "agent_update") {
          const summary = summarizeAgentEvent(event);
          setAgentMonitor((current) => {
            if (event.agent_name === "dicer") return { ...current, dicer: summary };
            if (event.agent_name === "npc_manager") return { ...current, npcManager: summary };
            if (event.agent_name === "director_state") return { ...current, directorState: summary };
            if (event.agent_name === "director") return { ...current, director: summary };
            return current;
          });
          return;
        }

        if (event.event === "narration_chunk") {
          finalNarration += event.delta;
          setMessages((current) => updateMessageContent(current, aiMessage.id, finalNarration));
          setAgentMonitor((current) => ({ ...current, narrator: finalNarration || "正在生成..." }));
          return;
        }

        if (event.event === "turn_result") {
          finalSession = event.session;
          finalNarration = event.turn.narration;
          sessionIdRef.current = event.session.session_id;
          setSession(event.session);
          setMessages((current) => updateMessageContent(current, aiMessage.id, finalNarration));
          setAgentMonitor((current) => ({
            ...current,
            dicer: event.turn.dicer_result ? formatJsonBlock(event.turn.dicer_result) : current.dicer,
            npcManager: event.turn.npc_result ? formatJsonBlock(event.turn.npc_result) : current.npcManager,
            directorState: event.turn.director_state_used ? formatJsonBlock(event.turn.director_state_used) : current.directorState,
            director: event.turn.next_director_result !== undefined ? formatJsonBlock(event.turn.next_director_result) : current.director,
            narrator: finalNarration,
          }));
          if (event.session.is_finished) {
            setMessages((current) => [...current, createMessage("system", "已达到本局最大对话轮次，请重新开始或先存档。")]);
          }
          return;
        }

        throw new Error(event.error);
      });

      if (!finalSession) {
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

  const handleSaveSession = async () => {
    if (!session) return;
    try {
      const response = await saveSession(session.session_id);
      await refreshSaves();
      setSelectedSaveFile(response.file_name);
      setMessages((current) => [...current, createMessage("system", `存档完成：${response.file_name}`)]);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "存档失败");
    }
  };

  const handleLoadSession = async () => {
    if (!selectedSaveFile) return;
    try {
      await resetForNewSession();
      const loaded = await loadSession(selectedSaveFile);
      sessionIdRef.current = loaded.session_id;
      setSession(loaded);
      setMessages([
        createMessage("system", `已读档：${selectedSaveFile}`),
        ...transcriptToMessages(loaded.transcript),
      ]);
      setAgentMonitor(emptyAgentMonitor);
      setInput("");
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "读档失败");
    }
  };

  const handleExportHistory = async () => {
    if (!session) return;
    try {
      const exported = await exportHistory(session.session_id);
      const url = URL.createObjectURL(exported.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = exported.fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setMessages((current) => [...current, createMessage("system", `历史已导出：${exported.fileName}`)]);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "导出历史失败");
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSend) return;
    const currentInput = input;
    setInput("");
    void sendMessage(currentInput);
  };

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!canSend) return;
      const currentInput = input;
      setInput("");
      void sendMessage(currentInput);
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

        <section className="app-body three-column">
          <aside className="sidebar app-card">
            <div className="sidebar-scroll">
              <section className="status-card config-card">
                <h3>玩家配置</h3>
                <form className="session-form" onSubmit={handleStartSession}>
                  <label>
                    玩家名
                    <input value={playerName} onChange={(event) => setPlayerName(event.target.value)} maxLength={24} />
                  </label>
                  <label>
                    规则
                    <select value={selectedRule} onChange={(event) => setSelectedRule(event.target.value)} disabled={loadingCatalog || startingSession}>
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
                        <option key={story.story_code} value={story.story_code} title={story.title || story.story_code}>
                          {truncateStoryTitle(story)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    最大对话轮次
                    <input type="number" min={1} max={200} value={maxTurns} onChange={(event) => setMaxTurns(Number(event.target.value) || 1)} />
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

              <PanelSection title="开场摘要" open={summaryOpen} onToggle={() => setSummaryOpen((value) => !value)}>
                <div className="story-rich-text compact">{renderRichText(storySummary)}</div>
              </PanelSection>

              <PanelSection title="当前状态" open={statusOpen} onToggle={() => setStatusOpen((value) => !value)}>
                <ul className="compact-status-list">
                  {status.map((item) => (
                    <li key={item.key}>
                      <span>{item.key}</span>
                      <strong>{item.value}</strong>
                    </li>
                  ))}
                </ul>
              </PanelSection>

              <PanelSection title="场景详情" open={sceneOpen} onToggle={() => setSceneOpen((value) => !value)}>
                <ul className="compact-status-list">
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
              </PanelSection>
            </div>
          </aside>

          <section className="chat app-card">
            <header className="chat-toolbar">
              <strong>会话记录</strong>
              <div className="chat-toolbar-actions">
                <label className="save-select">
                  <span>存档</span>
                  <select value={selectedSaveFile} onChange={(event) => setSelectedSaveFile(event.target.value)}>
                    <option value="">选择存档</option>
                    {saveEntries.map((entry) => (
                      <option key={entry.file_name} value={entry.file_name}>
                        {entry.file_name}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" className="toolbar-btn" onClick={() => void handleSaveSession()} disabled={!session}>
                  存档
                </button>
                <button type="button" className="toolbar-btn" onClick={() => void handleLoadSession()} disabled={!selectedSaveFile}>
                  读档
                </button>
                <button type="button" className="toolbar-btn" onClick={() => void handleExportHistory()} disabled={!session}>
                  导出历史
                </button>
                <label>
                  搜索
                  <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="关键词过滤" />
                </label>
              </div>
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
                onKeyDown={handleInputKeyDown}
                placeholder="行动示例：进入大厅观察壁画，询问守卫昨晚发生了什么"
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

          <aside className="agent-sidebar app-card">
            <header className="agent-header">
              <strong>Agent Monitor</strong>
              <span>实时输出</span>
            </header>
            <div className="agent-scroll">
              <section className="agent-card">
                <h3>Dicer</h3>
                <pre>{agentMonitor.dicer}</pre>
              </section>
              <section className="agent-card">
                <h3>NPC Manager</h3>
                <pre>{agentMonitor.npcManager}</pre>
              </section>
              <section className="agent-card">
                <h3>Director State</h3>
                <pre>{agentMonitor.directorState}</pre>
              </section>
              <section className="agent-card">
                <h3>Director</h3>
                <pre>{agentMonitor.director}</pre>
              </section>
              <section className="agent-card">
                <h3>Narrator</h3>
                <pre>{agentMonitor.narrator}</pre>
              </section>
            </div>
          </aside>
        </section>
      </main>
    </div>
  );
}
