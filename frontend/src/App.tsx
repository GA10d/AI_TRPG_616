import { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import parchmentBg from "../羊皮纸叙事.png";
import nightwatchBg from "../夜航控制台.png";
import neonBg from "../赛博霓虹.png";
import {
  CatalogResponse,
  fetchLanguagePack,
  LanguageOption,
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
type UiLanguage = "zh-CN" | "zh-TW" | "en" | "ja";

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

interface PanelProps {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  toggleText: string;
}

const COPY = {
  "zh-CN": {
    title: "Direct Play 控制台",
    playerConfig: "玩家配置",
    playerName: "玩家名",
    rule: "规则",
    story: "剧本",
    language: "语言",
    turns: "最大轮数",
    start: "开始会话",
    restart: "重新开始",
    creating: "创建中...",
    loading: "正在读取规则与剧本...",
    opening: "开场摘要",
    status: "当前状态",
    scene: "场景详情",
    save: "存档",
    load: "读档",
    export: "导出历史",
    search: "搜索",
    selectSave: "选择存档",
    player: "玩家",
    narrator: "主持人",
    system: "系统",
    send: "发送",
    sending: "发送中...",
    clear: "清空",
    startFirst: "请先开始会话",
    processing: "处理中",
    idle: "先在左侧选择规则、剧本和语言，然后开始会话。",
    createProgress: "正在创建会话并生成开场，请稍候...",
    turnProgress: "正在推进回合，主持人开始组织叙事...",
    sessionCreated: "会话已创建",
    saveDone: "存档完成",
    loadDone: "已读档",
    exportDone: "历史已导出",
    sessionFinished: "已达到本局最大轮数，请重新开始或先存档。",
    localizedPrompt: "该语言会启用增强 prompt。",
    fallbackPrompt: "该语言走通用 prompt，但 Narrator 会按所选语言输出。",
    none: "暂无",
    expand: "展开",
    collapse: "收起",
    monitor: "Agent Monitor",
    live: "实时输出",
    placeholder: "行动示例：进入大厅观察壁画，并询问守卫昨晚发生了什么？",
    themeParchment: "羊皮纸叙事",
    themeNightwatch: "夜航控制台",
    themeNeon: "赛博霓虹",
  },
  "zh-TW": {
    title: "Direct Play 控制台",
    playerConfig: "玩家設定",
    playerName: "玩家名",
    rule: "規則",
    story: "劇本",
    language: "語言",
    turns: "最大輪數",
    start: "開始會話",
    restart: "重新開始",
    creating: "建立中...",
    loading: "正在讀取規則與劇本...",
    opening: "開場摘要",
    status: "目前狀態",
    scene: "場景詳情",
    save: "存檔",
    load: "讀檔",
    export: "匯出歷史",
    search: "搜尋",
    selectSave: "選擇存檔",
    player: "玩家",
    narrator: "主持人",
    system: "系統",
    send: "送出",
    sending: "送出中...",
    clear: "清空",
    startFirst: "請先開始會話",
    processing: "處理中",
    idle: "先在左側選擇規則、劇本和語言，然後開始會話。",
    createProgress: "正在建立會話並生成開場，請稍候...",
    turnProgress: "正在推進回合，主持人開始組織敘事...",
    sessionCreated: "會話已建立",
    saveDone: "存檔完成",
    loadDone: "已讀檔",
    exportDone: "歷史已匯出",
    sessionFinished: "已達到本局最大輪數，請重新開始或先存檔。",
    localizedPrompt: "該語言會啟用增強 prompt。",
    fallbackPrompt: "該語言走通用 prompt，但 Narrator 會用所選語言輸出。",
    none: "暫無",
    expand: "展開",
    collapse: "收起",
    monitor: "Agent Monitor",
    live: "即時輸出",
    placeholder: "行動示例：進入大廳觀察壁畫，並詢問守衛昨晚發生了什麼？",
    themeParchment: "羊皮紙敘事",
    themeNightwatch: "夜航控制台",
    themeNeon: "賽博霓虹",
  },
  en: {
    title: "Direct Play Console",
    playerConfig: "Player Setup",
    playerName: "Player Name",
    rule: "Rule",
    story: "Story",
    language: "Language",
    turns: "Max Turns",
    start: "Start Session",
    restart: "Restart",
    creating: "Creating...",
    loading: "Loading rules and stories...",
    opening: "Opening",
    status: "State",
    scene: "Scene",
    save: "Save",
    load: "Load",
    export: "Export History",
    search: "Search",
    selectSave: "Select Save",
    player: "Player",
    narrator: "Narrator",
    system: "System",
    send: "Send",
    sending: "Sending...",
    clear: "Clear",
    startFirst: "Start a session first",
    processing: "Processing",
    idle: "Choose a rule, story, and language on the left, then start a session.",
    createProgress: "Creating the session and generating the opening...",
    turnProgress: "Advancing the turn. The narrator is organizing the scene...",
    sessionCreated: "Session created",
    saveDone: "Save complete",
    loadDone: "Loaded save",
    exportDone: "History exported",
    sessionFinished: "This run has reached its max turn count. Restart or save first.",
    localizedPrompt: "This language uses enhanced localized prompts.",
    fallbackPrompt: "This language uses the generic prompt path, but Narrator still outputs in the selected language.",
    none: "None",
    expand: "Expand",
    collapse: "Collapse",
    monitor: "Agent Monitor",
    live: "Live Output",
    placeholder: "Example action: Enter the hall, inspect the mural, and ask the guard what happened last night.",
    themeParchment: "Parchment",
    themeNightwatch: "Nightwatch",
    themeNeon: "Neon",
  },
  ja: {
    title: "Direct Play コンソール",
    playerConfig: "プレイヤー設定",
    playerName: "プレイヤー名",
    rule: "ルール",
    story: "シナリオ",
    language: "言語",
    turns: "最大ターン数",
    start: "セッション開始",
    restart: "やり直す",
    creating: "作成中...",
    loading: "利用可能なルールとシナリオを読み込み中...",
    opening: "導入",
    status: "現在状態",
    scene: "場面詳細",
    save: "保存",
    load: "読込",
    export: "履歴を書き出す",
    search: "検索",
    selectSave: "セーブ選択",
    player: "プレイヤー",
    narrator: "ナレーター",
    system: "システム",
    send: "送信",
    sending: "送信中...",
    clear: "クリア",
    startFirst: "先にセッションを開始してください",
    processing: "処理中",
    idle: "左側でルール、シナリオ、言語を選んでからセッションを開始してください。",
    createProgress: "セッションを作成し、導入を生成しています...",
    turnProgress: "ターンを進行中です。ナレーターが描写を組み立てています...",
    sessionCreated: "セッションを作成しました",
    saveDone: "保存しました",
    loadDone: "読込完了",
    exportDone: "履歴を出力しました",
    sessionFinished: "このセッションは最大ターン数に達しました。やり直すか保存してください。",
    localizedPrompt: "この言語では強化されたローカライズ prompt を使います。",
    fallbackPrompt: "この言語では汎用 prompt を使いますが、Narrator の出力言語は切り替わります。",
    none: "なし",
    expand: "展開",
    collapse: "折りたたむ",
    monitor: "Agent Monitor",
    live: "リアルタイム出力",
    placeholder: "行動例: ホールに入り、壁画を調べ、昨夜何が起きたかを衛兵に尋ねる。",
    themeParchment: "羊皮紙",
    themeNightwatch: "ナイトウォッチ",
    themeNeon: "ネオン",
  },
} as const;

const themeBackgrounds: Record<ThemeName, string> = {
  parchment: parchmentBg,
  nightwatch: nightwatchBg,
  neon: neonBg,
};

function normalizeUiLanguage(code: string): UiLanguage {
  const lowered = code.trim().toLowerCase();
  if (lowered.startsWith("zh-tw")) return "zh-TW";
  if (lowered.startsWith("ja")) return "ja";
  if (lowered.startsWith("en")) return "en";
  if (lowered.startsWith("zh")) return "zh-CN";
  return "en";
}

function createMessage(role: MessageRole, content: string, createdAt = Date.now()): ChatMessage {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${createdAt}-${Math.random()}`;
  return { id, role, content, createdAt };
}

function transcriptToMessages(transcript: TranscriptEntry[]): ChatMessage[] {
  return transcript
    .filter((entry) => entry.role === "system" || entry.role === "player" || entry.role === "ai")
    .map((entry, index) => createMessage(entry.role, entry.content, typeof entry.created_at === "number" ? entry.created_at : Date.now() + index));
}

function updateMessageContent(messages: ChatMessage[], id: string, content: string): ChatMessage[] {
  return messages.map((message) => (message.id === id ? { ...message, content } : message));
}

function renderRichText(text: string): ReactNode {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter(Boolean);
  return (
    <div className="rich-text">
      {lines.map((line, index) => (
        <p key={`${index}-${line}`} className="rich-text-paragraph">
          {line}
        </p>
      ))}
    </div>
  );
}

function summarizeAgentEvent(event: StreamTurnAgentEvent): string {
  try {
    return JSON.stringify(event.payload, null, 2);
  } catch {
    return String(event.payload);
  }
}

function Panel({ title, open, onToggle, children, toggleText }: PanelProps) {
  return (
    <section className="panel-card">
      <button type="button" className="panel-toggle" onClick={onToggle} aria-expanded={open}>
        <span>{title}</span>
        <strong>{toggleText}</strong>
      </button>
      {open ? <div className="panel-body">{children}</div> : null}
    </section>
  );
}

function getLanguageLabel(option: LanguageOption): string {
  return option.native_label === option.label ? `${option.native_label} (${option.code})` : `${option.native_label} / ${option.label} (${option.code})`;
}

function truncateStoryTitle(story: StoryOption): string {
  const source = story.title?.trim() || story.story_code;
  return source.length > 42 ? `${source.slice(0, 42)}...` : source;
}

export default function App() {
  const [theme, setTheme] = useState<ThemeName>("parchment");
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [playerName, setPlayerName] = useState("Player");
  const [selectedRule, setSelectedRule] = useState("");
  const [selectedStory, setSelectedStory] = useState("");
  const [selectedLanguage, setSelectedLanguage] = useState(localStorage.getItem("trpg-language") ?? "zh-CN");
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
  const [languageUi, setLanguageUi] = useState<Record<string, string> | null>(null);
  const [agentMonitor, setAgentMonitor] = useState<AgentMonitorState>({
    dicer: "",
    npcManager: "",
    directorState: "",
    director: "",
    narrator: "",
  });

  const uiLanguage = normalizeUiLanguage(selectedLanguage);
  const text = { ...COPY[uiLanguage], ...(languageUi ?? {}) };
  const messageListRef = useRef<HTMLElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const selectedRuleEntry = useMemo<RuleOption | undefined>(
    () => catalog?.rules.find((rule) => rule.rule_code === selectedRule),
    [catalog, selectedRule]
  );
  const stories = selectedRuleEntry?.stories ?? [];
  const selectedStoryEntry = useMemo<StoryOption | undefined>(
    () => stories.find((story) => story.story_code === selectedStory),
    [stories, selectedStory]
  );
  const selectedLanguageEntry = useMemo<LanguageOption | undefined>(
    () => catalog?.languages.find((language) => language.code === selectedLanguage),
    [catalog, selectedLanguage]
  );
  const filteredMessages = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return messages;
    return messages.filter((message) => message.content.toLowerCase().includes(keyword));
  }, [messages, search]);

  useEffect(() => {
    localStorage.setItem("trpg-language", selectedLanguage);
  }, [selectedLanguage]);

  useEffect(() => {
    let cancelled = false;
    fetchLanguagePack(selectedLanguage)
      .then((pack) => {
        if (!cancelled) setLanguageUi(pack.ui);
      })
      .catch(() => {
        if (!cancelled) setLanguageUi(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedLanguage]);

  useEffect(() => {
    let cancelled = false;
    fetchCatalog()
      .then((data) => {
        if (cancelled) return;
        setCatalog(data);
        setSelectedRule((current) => current || data.rules[0]?.rule_code || "");
        setSelectedStory((current) => current || data.rules[0]?.stories[0]?.story_code || "");
      })
      .catch((error) => {
        if (cancelled) return;
        setCatalogError(error instanceof Error ? error.message : "Catalog error");
      })
      .finally(() => {
        if (!cancelled) setLoadingCatalog(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void listSaves()
      .then((response) => {
        setSaveEntries(response.saves);
        setSelectedSaveFile((current) => current || response.saves[0]?.file_name || "");
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: "smooth" });
  }, [filteredMessages]);

  useEffect(() => {
    return () => {
      const sessionId = sessionIdRef.current;
      if (sessionId) void deleteSession(sessionId).catch(() => undefined);
    };
  }, []);

  const resetForNewSession = async () => {
    const oldSessionId = sessionIdRef.current;
    if (oldSessionId) await deleteSession(oldSessionId).catch(() => undefined);
    sessionIdRef.current = null;
    setSession(null);
  };

  const handleStartSession = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedRule || !selectedStory || !selectedLanguage) return;

    setStartingSession(true);
    setOperationError("");
    setMessages([createMessage("system", text.createProgress)]);
    setAgentMonitor({ dicer: "", npcManager: "", directorState: "", director: "", narrator: "" });

    try {
      await resetForNewSession();
      await streamCreateSession(
        {
          rule_code: selectedRule,
          story_code: selectedStory,
          player_name: playerName.trim() || text.player,
          language_code: selectedLanguage,
          max_turns: maxTurns,
        },
        (streamEvent) => {
          if (streamEvent.event === "runtime_log") {
            setMessages((current) => [...current, createMessage("system", streamEvent.message)]);
            return;
          }
          if (streamEvent.event === "agent_update") {
            const summary = summarizeAgentEvent(streamEvent);
            setAgentMonitor((current) => {
              if (streamEvent.agent_name === "dicer") return { ...current, dicer: summary };
              if (streamEvent.agent_name === "npc_manager") return { ...current, npcManager: summary };
              if (streamEvent.agent_name === "director_state") return { ...current, directorState: summary };
              if (streamEvent.agent_name === "director") return { ...current, director: summary };
              return current;
            });
            return;
          }
          if (streamEvent.event === "session_ready") {
            sessionIdRef.current = streamEvent.session.session_id;
            setSession(streamEvent.session);
            setSelectedLanguage(streamEvent.session.language_code);
            setMessages((current) => [
              ...current,
              createMessage("system", `${text.sessionCreated}: ${streamEvent.session.state.scenario.title || streamEvent.session.story_code}`),
              ...transcriptToMessages(streamEvent.session.transcript),
            ]);
            return;
          }
          throw new Error(streamEvent.error);
        }
      );
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : text.processing);
    } finally {
      setStartingSession(false);
    }
  };

  const sendMessage = async (rawText: string) => {
    if (!session || !rawText.trim()) return;
    setSubmittingTurn(true);
    setOperationError("");

    const playerMessage = createMessage("player", rawText.trim());
    const aiMessage = createMessage("ai", "");
    setMessages((current) => [...current, playerMessage, aiMessage]);

    try {
      await streamTurn(session.session_id, rawText.trim(), (event) => {
        if (event.event === "turn_start") {
          setMessages((current) => [...current, createMessage("system", text.turnProgress)]);
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
          setMessages((current) => updateMessageContent(current, aiMessage.id, current.find((item) => item.id === aiMessage.id)?.content + event.delta || event.delta));
          return;
        }
        if (event.event === "turn_result") {
          setSession(event.session);
          setSelectedLanguage(event.session.language_code);
          setMessages((current) => updateMessageContent(current, aiMessage.id, event.turn.narration));
          setAgentMonitor((current) => ({
            ...current,
            dicer: event.turn.dicer_result ? JSON.stringify(event.turn.dicer_result, null, 2) : current.dicer,
            npcManager: event.turn.npc_result ? JSON.stringify(event.turn.npc_result, null, 2) : current.npcManager,
            directorState: event.turn.director_state_used ? JSON.stringify(event.turn.director_state_used, null, 2) : current.directorState,
            director: event.turn.next_director_result ? JSON.stringify(event.turn.next_director_result, null, 2) : current.director,
            narrator: event.turn.narration,
          }));
          if (event.session.is_finished) setMessages((current) => [...current, createMessage("system", text.sessionFinished)]);
          return;
        }
        throw new Error(event.error);
      });
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : text.processing);
    } finally {
      setSubmittingTurn(false);
    }
  };

  const handleLoadSession = async () => {
    if (!selectedSaveFile) return;
    try {
      await resetForNewSession();
      const loaded = await loadSession(selectedSaveFile);
      sessionIdRef.current = loaded.session_id;
      setSession(loaded);
      setSelectedLanguage(loaded.language_code);
      setMessages([createMessage("system", `${text.loadDone}: ${selectedSaveFile}`), ...transcriptToMessages(loaded.transcript)]);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : text.processing);
    }
  };

  const handleSaveSession = async () => {
    if (!session) return;
    try {
      const response = await saveSession(session.session_id);
      setMessages((current) => [...current, createMessage("system", `${text.saveDone}: ${response.file_name}`)]);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : text.processing);
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
      setMessages((current) => [...current, createMessage("system", `${text.exportDone}: ${exported.fileName}`)]);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : text.processing);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!input.trim()) return;
    const currentInput = input;
    setInput("");
    void sendMessage(currentInput);
  };

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!input.trim()) return;
      const currentInput = input;
      setInput("");
      void sendMessage(currentInput);
    }
  };

  const themeLabels = {
    parchment: text.themeParchment,
    nightwatch: text.themeNightwatch,
    neon: text.themeNeon,
  };

  const storySummary = session?.opening || selectedStoryEntry?.opening_scene || text.idle;

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
            <h1>{text.title}</h1>
          </div>
          <div className="theme-switcher" role="tablist" aria-label="Theme switcher">
            {(Object.keys(themeLabels) as ThemeName[]).map((themeName) => (
              <button key={themeName} type="button" className={theme === themeName ? "active" : ""} onClick={() => setTheme(themeName)}>
                {themeLabels[themeName]}
              </button>
            ))}
          </div>
        </header>

        <section className="app-body three-column">
          <aside className="sidebar app-card">
            <div className="sidebar-scroll">
              <section className="status-card config-card">
                <h3>{text.playerConfig}</h3>
                <form className="session-form" onSubmit={handleStartSession}>
                  <label>
                    {text.playerName}
                    <input value={playerName} onChange={(event) => setPlayerName(event.target.value)} />
                  </label>
                  <label>
                    {text.rule}
                    <select value={selectedRule} onChange={(event) => setSelectedRule(event.target.value)} disabled={loadingCatalog || startingSession}>
                      {catalog?.rules.map((rule) => (
                        <option key={rule.rule_code} value={rule.rule_code}>
                          {rule.rule_code}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {text.story}
                    <select value={selectedStory} onChange={(event) => setSelectedStory(event.target.value)} disabled={loadingCatalog || startingSession}>
                      {stories.map((story) => (
                        <option key={story.story_code} value={story.story_code}>
                          {truncateStoryTitle(story)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {text.language}
                    <select value={selectedLanguage} onChange={(event) => setSelectedLanguage(event.target.value)} disabled={loadingCatalog || startingSession}>
                      {catalog?.languages.map((language) => (
                        <option key={language.code} value={language.code}>
                          {getLanguageLabel(language)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {text.turns}
                    <input type="number" min={1} max={200} value={maxTurns} onChange={(event) => setMaxTurns(Number(event.target.value) || 1)} />
                  </label>
                  {selectedLanguageEntry ? <p className="form-note">{selectedLanguageEntry.prompt_localized ? text.localizedPrompt : text.fallbackPrompt}</p> : null}
                  <div className="session-form-actions">
                    <button type="submit">{startingSession ? text.creating : session ? text.restart : text.start}</button>
                  </div>
                </form>
                {loadingCatalog ? <p className="form-note">{text.loading}</p> : null}
                {catalogError ? <p className="form-note error">{catalogError}</p> : null}
                {operationError ? <p className="form-note error">{operationError}</p> : null}
              </section>

              <Panel title={text.opening} open={summaryOpen} onToggle={() => setSummaryOpen((value) => !value)} toggleText={summaryOpen ? text.collapse : text.expand}>
                <div className="story-rich-text compact">{renderRichText(storySummary)}</div>
              </Panel>

              <Panel title={text.status} open={statusOpen} onToggle={() => setStatusOpen((value) => !value)} toggleText={statusOpen ? text.collapse : text.expand}>
                <div className="panel-body">
                  <p>{session?.state.scene.location || text.none}</p>
                  <p>{session ? `${session.turns_remaining}/${session.max_turns}` : "-"}</p>
                </div>
              </Panel>

              <Panel title={text.scene} open={sceneOpen} onToggle={() => setSceneOpen((value) => !value)} toggleText={sceneOpen ? text.collapse : text.expand}>
                <div className="panel-body">
                  <p>{session?.state.scene.description || text.none}</p>
                </div>
              </Panel>
            </div>
          </aside>

          <section className="chat app-card">
            <header className="chat-toolbar">
              <strong>{text.title}</strong>
              <div className="chat-toolbar-actions">
                <label className="save-select">
                  <span>{text.save}</span>
                  <select value={selectedSaveFile} onChange={(event) => setSelectedSaveFile(event.target.value)}>
                    <option value="">{text.selectSave}</option>
                    {saveEntries.map((entry) => (
                      <option key={entry.file_name} value={entry.file_name}>
                        {entry.file_name}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" className="toolbar-btn" onClick={() => void handleSaveSession()} disabled={!session}>
                  {text.save}
                </button>
                <button type="button" className="toolbar-btn" onClick={() => void handleLoadSession()} disabled={!selectedSaveFile}>
                  {text.load}
                </button>
                <button type="button" className="toolbar-btn" onClick={() => void handleExportHistory()} disabled={!session}>
                  {text.export}
                </button>
                <label>
                  {text.search}
                  <input value={search} onChange={(event) => setSearch(event.target.value)} />
                </label>
              </div>
            </header>

            <section className="message-list" ref={messageListRef}>
              {filteredMessages.length === 0 ? (
                <article className="chat-bubble system">
                  <header>
                    <strong>{text.system}</strong>
                  </header>
                  <div className="chat-bubble-body">
                    <p>{text.idle}</p>
                  </div>
                </article>
              ) : null}

              {filteredMessages.map((message) => (
                <article key={message.id} className={`chat-bubble ${message.role}`}>
                  <header>
                    <strong>{message.role === "player" ? text.player : message.role === "ai" ? text.narrator : text.system}</strong>
                  </header>
                  <div className="chat-bubble-body">{renderRichText(message.content)}</div>
                </article>
              ))}

              {(startingSession || submittingTurn) && (
                <article className="chat-bubble ai typing">
                  <header>
                    <strong>{text.narrator}</strong>
                    <span>{text.processing}</span>
                  </header>
                  <div className="chat-bubble-body">
                    <p>{startingSession ? text.createProgress : text.turnProgress}</p>
                  </div>
                </article>
              )}
            </section>

            <form onSubmit={handleSubmit} className="composer">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={text.placeholder}
                rows={4}
                disabled={!session || session.is_finished || startingSession}
              />
              <div className="composer-footer">
                <span>{session ? `${session.turns_used}/${session.max_turns}` : text.startFirst}</span>
                <div className="composer-actions">
                  <button type="button" onClick={() => setInput("")}>
                    {text.clear}
                  </button>
                  <button type="submit" disabled={!session || submittingTurn}>
                    {submittingTurn ? text.sending : text.send}
                  </button>
                </div>
              </div>
            </form>
          </section>

          <aside className="agent-sidebar app-card">
            <header className="agent-header">
              <strong>{text.monitor}</strong>
              <span>{text.live}</span>
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
