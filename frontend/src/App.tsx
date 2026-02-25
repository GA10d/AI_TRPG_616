import { FormEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import "./App.css";

type MessageRole = "player" | "ai";

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

const initialState: ChatState = {
  messages: [
    {
      id: "seed-1",
      role: "ai",
      content: "欢迎。你站在雾气笼罩的古桥前，第一步想做什么？",
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
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;

  return {
    id,
    role,
    content,
    createdAt: Date.now(),
  };
}

function buildAiReply(playerInput: string): string {
  return `【AI主持】你尝试“${playerInput}”。周围环境出现了新的线索，你要继续调查、交涉，还是直接行动？`;
}

export default function App() {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const [input, setInput] = useState("");
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const listRef = useRef<HTMLElement | null>(null);

  const canSend = useMemo(() => input.trim().length > 0 && !state.isAiTyping, [input, state.isAiTyping]);

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
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [state.messages, state.isAiTyping]);

  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, []);

  return (
    <main className="chat-shell">
      <header className="chat-header">
        <h1>AI TRPG</h1>
      </header>

      <section className="chat-list" ref={listRef} aria-live="polite">
        {state.messages.map((message) => (
          <article key={message.id} className={`bubble ${message.role}`}>
            <span className="role">{message.role === "player" ? "玩家" : "AI主持"}</span>
            <p>{message.content}</p>
          </article>
        ))}

        {state.isAiTyping && (
          <article className="bubble ai typing">
            <span className="role">AI主持</span>
            <p>正在思考...</p>
          </article>
        )}
      </section>

      <form className="chat-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入你的行动..."
          aria-label="消息输入框"
        />
        <button type="submit" disabled={!canSend}>
          发送
        </button>
      </form>
    </main>
  );
}
