import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import "./App.css";
import parchmentBg from "../羊皮纸叙事.png";
import nightwatchBg from "../夜航控制台.png";
import neonBg from "../赛博霓虹.png";
import { generateStoryImage, StoryImageTrigger, T2ICharacterRef } from "./t2i/t2i_client";

type MessageRole = "system" | "player" | "ai";
type ThemeName = "parchment" | "nightwatch" | "neon";
type ThemeBackground = Record<ThemeName, string>;
type MessageKind = "text" | "image";
type ImageStatus = "pending" | "ready" | "error";

interface ChatMessage {
  id: string;
  role: MessageRole;
  kind: MessageKind;
  content: string;
  createdAt: number;
  imageUrl?: string;
  imageStatus?: ImageStatus;
  imageProvider?: string;
  triggerType?: StoryImageTrigger;
}

interface CharacterProfile {
  id: string;
  name: string;
  appearance: string;
  portraitUrl?: string;
  createdAt: number;
  lastUsedTurn: number;
}

interface CharacterDraft {
  name: string;
  gender: "男" | "女" | "非二元";
  ageBand: "少年" | "青年" | "中年" | "老年";
  hairstyle: "短发" | "长发" | "马尾" | "卷发" | "光头";
  hairColor: "黑色" | "棕色" | "金色" | "银白" | "红色";
  outfit: "风衣" | "轻甲" | "长袍" | "制服" | "便装";
  weapon: "长剑" | "手枪" | "法杖" | "匕首" | "无武器";
  mood: "冷静" | "严肃" | "温和" | "狂气" | "神秘";
  extra: string;
}

interface ChatState {
  messages: ChatMessage[];
  isAiTyping: boolean;
}

type ChatAction =
  | { type: "append"; payload: ChatMessage }
  | { type: "patch"; payload: { id: string; patch: Partial<ChatMessage> } }
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

const IMAGE_COOLDOWN_TURNS = 3;
const IMAGE_LIMIT_PER_SESSION = 12;
const CHARACTER_STORE_KEY = "trpg-character-roster-v3";
const LEGACY_CHARACTER_STORE_KEYS = ["trpg-character-roster-v1", "trpg-character-roster-v2"];
const defaultCharacterDraft: CharacterDraft = {
  name: "",
  gender: "男",
  ageBand: "青年",
  hairstyle: "短发",
  hairColor: "黑色",
  outfit: "风衣",
  weapon: "长剑",
  mood: "冷静",
  extra: "",
};

const initialState: ChatState = {
  messages: [
    {
      id: "seed-0",
      role: "system",
      kind: "text",
      content: "场景载入完成：风暴将在 3 小时后抵达黑砂古城。",
      createdAt: Date.now() - 60_000,
    },
    {
      id: "seed-1",
      role: "ai",
      kind: "text",
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
    case "patch":
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.payload.id ? { ...message, ...action.payload.patch } : message
        ),
      };
    case "setTyping":
      return { ...state, isAiTyping: action.payload };
    default:
      return state;
  }
}

function createMessage(role: MessageRole, content: string, kind: MessageKind = "text"): ChatMessage {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return { id, role, kind, content, createdAt: Date.now() };
}

function createCharacter(name: string, appearance: string): CharacterProfile {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `char-${Date.now()}-${Math.random()}`;
  return {
    id,
    name,
    appearance,
    createdAt: Date.now(),
    lastUsedTurn: 0,
  };
}

function loadCharacters(): CharacterProfile[] {
  const raw = localStorage.getItem(CHARACTER_STORE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as Array<Partial<CharacterProfile>>;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.name === "string" && typeof item.appearance === "string")
      .map((item) => ({
        id: typeof item.id === "string" ? item.id : createCharacter(item.name as string, item.appearance as string).id,
        name: item.name as string,
        appearance: item.appearance as string,
        portraitUrl: typeof item.portraitUrl === "string" ? item.portraitUrl : undefined,
        createdAt: typeof item.createdAt === "number" ? item.createdAt : Date.now(),
        lastUsedTurn: typeof item.lastUsedTurn === "number" ? item.lastUsedTurn : 0,
      }));
  } catch {
    return [];
  }
}

function buildAiReply(playerInput: string): string {
  return `你尝试“${playerInput}”。地面传来轻微震动，远处有金属反光。你要继续靠近，还是先观察周边地形？`;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function buildCharacterAppearance(draft: CharacterDraft): string {
  const parts = [
    `${draft.ageBand}${draft.gender}`,
    `${draft.hairColor}${draft.hairstyle}`,
    `服装：${draft.outfit}`,
    `武器：${draft.weapon}`,
    `气质：${draft.mood}`,
  ];
  const extra = draft.extra.trim();
  if (extra) parts.push(`补充设定：${extra}`);
  return parts.join("，");
}

function detectImageTrigger(input: string): { trigger: StoryImageTrigger; subject: string } | null {
  const manual = input.match(/^\s*\/img\s+(.+)/i);
  if (manual?.[1]) {
    return { trigger: "manual", subject: manual[1].trim() };
  }

  const scene = /(进入|前往|抵达|探索|调查|观察|查看).*(房间|遗迹|古城|入口|大厅|走廊|地下|营地|酒馆|教堂|森林|沙漠)/;
  const npc = /(遇到|看见|发现|交谈|询问|接触).*(NPC|男人|女人|守卫|祭司|商人|队友|陌生人)/;

  if (scene.test(input)) return { trigger: "scene_shift", subject: input.trim() };
  if (npc.test(input)) return { trigger: "npc_intro", subject: input.trim() };
  return null;
}

function buildImagePrompt(
  trigger: StoryImageTrigger,
  subject: string,
  theme: ThemeName,
  characters: T2ICharacterRef[]
): string {
  const styleByTheme: Record<ThemeName, string> = {
    parchment: "cinematic natural light, realistic travel journal illustration",
    nightwatch: "low-key dramatic lighting, tactical console visualization",
    neon: "high contrast neon, cyberpunk cinematic concept art",
  };

  const castLine =
    characters.length > 0
      ? `Include these recurring characters consistently: ${characters
          .map((c) => `${c.name}(${c.appearance})`)
          .join("; ")}.`
      : "";

  if (trigger === "character_portrait") {
    return `TRPG character portrait card, vertical composition, portrait orientation 2:3, full-body, clear silhouette, ${subject}, ${styleByTheme[theme]}, no watermark, no text`;
  }

  if (trigger === "npc_intro") {
    return `TRPG NPC portrait card, half-body, clear silhouette, ${subject}, ${castLine} ${styleByTheme[theme]}, no watermark, no text`;
  }

  if (trigger === "manual") {
    return `${subject}, ${castLine} TRPG scene art, ${styleByTheme[theme]}, no watermark, no text`;
  }

  return `TRPG exploration scene, wide shot environment, ${subject}, ${castLine} ${styleByTheme[theme]}, no watermark, no text`;
}

function toCharacterRefs(characters: CharacterProfile[]): T2ICharacterRef[] {
  return characters.map((c) => ({ name: c.name, appearance: c.appearance, portraitUrl: c.portraitUrl }));
}

function pickSceneCharacters(input: string, roster: CharacterProfile[]): CharacterProfile[] {
  if (roster.length === 0) return [];

  const byMention = roster.filter((c) => input.includes(c.name));
  if (byMention.length > 0) return byMention.slice(0, 3);

  return [...roster].sort((a, b) => b.lastUsedTurn - a.lastUsedTurn || b.createdAt - a.createdAt).slice(0, 2);
}

export default function App() {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null);
  const [isCreatorOpen, setIsCreatorOpen] = useState(false);
  const [isCreatingCharacter, setIsCreatingCharacter] = useState(false);
  const [draft, setDraft] = useState<CharacterDraft>(defaultCharacterDraft);
  const [characters, setCharacters] = useState<CharacterProfile[]>(() => loadCharacters());
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
  const turnRef = useRef(0);
  const generatedImageRef = useRef(0);
  const lastImageTurnRef = useRef(-999);
  const charactersRef = useRef<CharacterProfile[]>(characters);

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

  const requestSceneImage = useCallback(
    (playerInput: string, trigger: StoryImageTrigger, sceneCharacters: CharacterProfile[]) => {
      const pending = createMessage("system", "正在根据剧情生成场景图...", "image");
      pending.imageStatus = "pending";
      pending.triggerType = trigger;
      dispatch({ type: "append", payload: pending });

      const refs = toCharacterRefs(sceneCharacters);
      const prompt = buildImagePrompt(trigger, playerInput, theme, refs);
      const sceneId = `session-${Math.floor(turnRef.current / 2)}-${trigger}`;

      void generateStoryImage({
        prompt,
        trigger,
        theme,
        sceneId,
        characters: refs,
      })
        .then((result) => {
          dispatch({
            type: "patch",
            payload: {
              id: pending.id,
              patch: {
                imageStatus: "ready",
                imageUrl: result.imageUrl,
                imageProvider: result.provider,
                content:
                  sceneCharacters.length > 0
                    ? `剧情配图已生成（含角色：${sceneCharacters.map((c) => c.name).join("、")}）`
                    : result.cached
                      ? "剧情配图（缓存复用）"
                      : "剧情配图已生成",
              },
            },
          });
        })
        .catch(() => {
          dispatch({
            type: "patch",
            payload: {
              id: pending.id,
              patch: {
                imageStatus: "error",
                content: "文生图失败，已降级为文字推进。",
              },
            },
          });
        });
    },
    [theme]
  );

  const requestCharacterPortrait = useCallback(
    async (name: string, appearance: string): Promise<boolean> => {
      const reachedLimit = generatedImageRef.current >= IMAGE_LIMIT_PER_SESSION;
      if (reachedLimit) {
        dispatch({ type: "append", payload: createMessage("system", "本局配图额度已用完，无法再生成角色立绘。") });
        return false;
      }

      generatedImageRef.current += 1;

      const pending = createMessage("system", `正在生成角色立绘：${name}`, "image");
      pending.imageStatus = "pending";
      pending.triggerType = "character_portrait";
      dispatch({ type: "append", payload: pending });

      const character = createCharacter(name, appearance);
      const refs = toCharacterRefs([character]);
      const prompt = buildImagePrompt("character_portrait", `${name}, ${appearance}`, theme, refs);

      try {
        const result = await generateStoryImage({
        prompt,
        trigger: "character_portrait",
        theme,
        sceneId: `portrait-${name}`,
        characters: refs,
        allowFallback: false,
        });

        dispatch({
          type: "patch",
          payload: {
            id: pending.id,
            patch: {
              imageStatus: "ready",
              imageUrl: result.imageUrl,
              imageProvider: result.provider,
              content: `角色立绘已生成：${name}`,
            },
          },
        });

        setCharacters((prev) => {
          const next = prev.filter((item) => item.name !== name);
          next.unshift({ ...character, portraitUrl: result.imageUrl, lastUsedTurn: turnRef.current });
          return next;
        });

        dispatch({ type: "append", payload: createMessage("system", `角色“${name}”已加入立绘库，后续场景图会优先嵌入该角色。`) });
        return true;
      } catch (error) {
        const reason = error instanceof Error ? error.message : "未知错误";
        dispatch({
          type: "patch",
          payload: {
            id: pending.id,
            patch: {
              imageStatus: "error",
              content: `角色“${name}”立绘生成失败：${reason}`,
            },
          },
        });
        return false;
      }
    },
    [theme]
  );

  const updateDraft = useCallback(<K extends keyof CharacterDraft>(key: K, value: CharacterDraft[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }, []);

  const openCreator = useCallback(() => {
    setDraft(defaultCharacterDraft);
    setIsCreatorOpen(true);
  }, []);

  const closeCreator = useCallback(() => {
    if (isCreatingCharacter) return;
    setIsCreatorOpen(false);
  }, [isCreatingCharacter]);

  const handleCreateCharacter = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = draft.name.trim();
      if (!name) return;

      setIsCreatingCharacter(true);
      const appearance = buildCharacterAppearance(draft);
      const ok = await requestCharacterPortrait(name, appearance);
      setIsCreatingCharacter(false);
      if (ok) setIsCreatorOpen(false);
    },
    [draft, requestCharacterPortrait]
  );

  const sendMessage = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      turnRef.current += 1;
      dispatch({ type: "append", payload: createMessage("player", trimmed) });

      dispatch({ type: "setTyping", payload: true });

      const timer = setTimeout(() => {
        dispatch({ type: "append", payload: createMessage("ai", buildAiReply(trimmed)) });
        dispatch({ type: "setTyping", payload: false });

        const triggerInfo = detectImageTrigger(trimmed);
        if (!triggerInfo) return;

        const reachedLimit = generatedImageRef.current >= IMAGE_LIMIT_PER_SESSION;
        if (reachedLimit) {
          dispatch({ type: "append", payload: createMessage("system", "本局配图额度已用完（12/12），继续以文字叙事推进。") });
          return;
        }

        const bypassCooldown = triggerInfo.trigger === "manual";
        const cooldownReady = turnRef.current - lastImageTurnRef.current >= IMAGE_COOLDOWN_TURNS;
        if (!bypassCooldown && !cooldownReady) {
          dispatch({ type: "append", payload: createMessage("system", "已命中文生图触发，但当前处于冷却中（每 3 回合最多 1 张）。") });
          return;
        }

        const sceneCharacters = pickSceneCharacters(trimmed, charactersRef.current);
        if (sceneCharacters.length > 0) {
          setCharacters((prev) =>
            prev.map((c) =>
              sceneCharacters.some((picked) => picked.id === c.id) ? { ...c, lastUsedTurn: turnRef.current } : c
            )
          );
        }

        generatedImageRef.current += 1;
        lastImageTurnRef.current = turnRef.current;
        requestSceneImage(triggerInfo.subject, triggerInfo.trigger, sceneCharacters);
      }, 1000);

      timersRef.current.push(timer);
    },
    [requestSceneImage]
  );

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      sendMessage(input);
      setInput("");
    },
    [input, sendMessage]
  );

  const onInputKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
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
    LEGACY_CHARACTER_STORE_KEYS.forEach((key) => localStorage.removeItem(key));
  }, []);

  useEffect(() => {
    // Persist only metadata; portrait data URLs are too large for localStorage.
    const compact = characters.map((item) => ({
      id: item.id,
      name: item.name,
      appearance: item.appearance,
      portraitUrl: item.portraitUrl && !item.portraitUrl.startsWith("data:") ? item.portraitUrl : undefined,
      createdAt: item.createdAt,
      lastUsedTurn: item.lastUsedTurn,
    }));
    try {
      localStorage.setItem(CHARACTER_STORE_KEY, JSON.stringify(compact));
    } catch {
      localStorage.removeItem(CHARACTER_STORE_KEY);
    }
    charactersRef.current = characters;
  }, [characters]);

  useEffect(() => {
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: "smooth" });
  }, [state.messages, state.isAiTyping]);

  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreviewImage(null);
        if (!isCreatingCharacter) setIsCreatorOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isCreatingCharacter]);

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

            <section className="character-card">
              <div className="character-toolbar">
                <h3>角色立绘库</h3>
                <button type="button" className="character-add-btn" onClick={openCreator}>
                  新增角色
                </button>
              </div>
              <p>创建后会自动在后续场景图中嵌入该角色。</p>
              {characters.length === 0 ? (
                <div className="character-empty">暂无角色立绘</div>
              ) : (
                <ul className="character-list">
                  {characters.map((character) => (
                    <li key={character.id}>
                      <button
                        type="button"
                        className="portrait-frame"
                        onClick={() => character.portraitUrl && setPreviewImage({ url: character.portraitUrl, alt: character.name })}
                        title={character.appearance}
                      >
                        {character.portraitUrl ? (
                          <img src={character.portraitUrl} alt={character.name} loading="lazy" />
                        ) : (
                          <div className="character-skeleton" />
                        )}
                        <span className="portrait-nameplate">{character.name}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
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
                <article key={message.id} className={`chat-bubble ${message.role} ${message.kind === "image" ? "image" : ""}`}>
                  <header>
                    <strong>{message.role === "player" ? "玩家" : message.role === "ai" ? "主持人" : "系统"}</strong>
                    <span>{formatTime(message.createdAt)}</span>
                  </header>

                  {message.kind === "image" ? (
                    <>
                      {message.imageStatus === "ready" && message.imageUrl ? (
                        <figure className="image-card">
                          <button
                            type="button"
                            className="image-open"
                            onClick={() => setPreviewImage({ url: message.imageUrl as string, alt: message.content })}
                          >
                            <img src={message.imageUrl} alt={message.content} loading="lazy" />
                          </button>
                          <figcaption>
                            {message.content}
                            {message.imageProvider ? <em>来源: {message.imageProvider}</em> : null}
                          </figcaption>
                          <small className="image-hint">点击图片可放大查看</small>
                        </figure>
                      ) : (
                        <p>{message.content}</p>
                      )}
                    </>
                  ) : (
                    <p>{message.content}</p>
                  )}
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
                placeholder="行动示例：进入大厅观察壁画 | /img 沙暴中的古城全景"
                rows={4}
              />
              <div className="composer-footer">
                <span>Enter 发送 | Shift+Enter 换行 | /img 手动生图 | 角色库按钮新增立绘</span>
                <div className="composer-actions">
                  <button type="button" onClick={() => setInput("")}>清空</button>
                  <button type="submit" disabled={!canSend}>发送</button>
                </div>
              </div>
            </form>
          </section>
        </section>
      </main>

      {isCreatorOpen ? (
        <div className="character-modal" role="dialog" aria-modal="true" onClick={closeCreator}>
          <section className="character-modal-panel" onClick={(event) => event.stopPropagation()}>
            <header>
              <strong>新增角色立绘</strong>
              <button type="button" onClick={closeCreator} disabled={isCreatingCharacter}>
                取消
              </button>
            </header>

            <form className="character-form" onSubmit={handleCreateCharacter}>
              <label>
                角色名
                <input
                  value={draft.name}
                  onChange={(event) => updateDraft("name", event.target.value)}
                  placeholder="例如：林雾"
                  maxLength={24}
                  required
                />
              </label>

              <label>
                性别
                <select value={draft.gender} onChange={(event) => updateDraft("gender", event.target.value as CharacterDraft["gender"])}>
                  <option value="男">男</option>
                  <option value="女">女</option>
                  <option value="非二元">非二元</option>
                </select>
              </label>

              <label>
                年龄段
                <select value={draft.ageBand} onChange={(event) => updateDraft("ageBand", event.target.value as CharacterDraft["ageBand"])}>
                  <option value="少年">少年</option>
                  <option value="青年">青年</option>
                  <option value="中年">中年</option>
                  <option value="老年">老年</option>
                </select>
              </label>

              <label>
                发型
                <select value={draft.hairstyle} onChange={(event) => updateDraft("hairstyle", event.target.value as CharacterDraft["hairstyle"])}>
                  <option value="短发">短发</option>
                  <option value="长发">长发</option>
                  <option value="马尾">马尾</option>
                  <option value="卷发">卷发</option>
                  <option value="光头">光头</option>
                </select>
              </label>

              <label>
                发色
                <select value={draft.hairColor} onChange={(event) => updateDraft("hairColor", event.target.value as CharacterDraft["hairColor"])}>
                  <option value="黑色">黑色</option>
                  <option value="棕色">棕色</option>
                  <option value="金色">金色</option>
                  <option value="银白">银白</option>
                  <option value="红色">红色</option>
                </select>
              </label>

              <label>
                武器
                <select value={draft.weapon} onChange={(event) => updateDraft("weapon", event.target.value as CharacterDraft["weapon"])}>
                  <option value="长剑">长剑</option>
                  <option value="手枪">手枪</option>
                  <option value="法杖">法杖</option>
                  <option value="匕首">匕首</option>
                  <option value="无武器">无武器</option>
                </select>
              </label>

              <label>
                服装
                <select value={draft.outfit} onChange={(event) => updateDraft("outfit", event.target.value as CharacterDraft["outfit"])}>
                  <option value="风衣">风衣</option>
                  <option value="轻甲">轻甲</option>
                  <option value="长袍">长袍</option>
                  <option value="制服">制服</option>
                  <option value="便装">便装</option>
                </select>
              </label>

              <label>
                气质
                <select value={draft.mood} onChange={(event) => updateDraft("mood", event.target.value as CharacterDraft["mood"])}>
                  <option value="冷静">冷静</option>
                  <option value="严肃">严肃</option>
                  <option value="温和">温和</option>
                  <option value="狂气">狂气</option>
                  <option value="神秘">神秘</option>
                </select>
              </label>

              <label className="character-form-wide">
                自由发挥
                <textarea
                  value={draft.extra}
                  onChange={(event) => updateDraft("extra", event.target.value)}
                  placeholder="可写：伤疤、饰品、职业、神态、背景故事、色彩偏好等"
                  rows={3}
                />
              </label>

              <footer>
                <span>{buildCharacterAppearance(draft)}</span>
                <button type="submit" disabled={isCreatingCharacter || !draft.name.trim()}>
                  {isCreatingCharacter ? "生成中..." : "生成立绘"}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {previewImage ? (
        <div className="image-lightbox" role="dialog" aria-modal="true" onClick={() => setPreviewImage(null)}>
          <button type="button" className="lightbox-close" onClick={() => setPreviewImage(null)} aria-label="关闭预览">
            关闭
          </button>
          <img
            className="lightbox-image"
            src={previewImage.url}
            alt={previewImage.alt}
            onClick={(event) => event.stopPropagation()}
          />
        </div>
      ) : null}
    </div>
  );
}

