"use client";

import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { advisorApi, portfolioApi } from "@/lib/api";
import type { Portfolio, Conversation, ChatMessage } from "@/types";
import toast from "react-hot-toast";

export default function AdvisorPage() {
  const qc = useQueryClient();
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [selectedPortfolio, setSelectedPortfolio] = useState<number | undefined>();
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: portfolios = [] } = useQuery<Portfolio[]>({
    queryKey: ["portfolios"],
    queryFn: () => portfolioApi.list().then((r) => r.data),
  });

  const { data: conversations = [] } = useQuery<Conversation[]>({
    queryKey: ["conversations"],
    queryFn: () => advisorApi.conversations().then((r) => r.data),
  });

  const { data: activeConv } = useQuery<Conversation>({
    queryKey: ["conversation", activeConvId],
    queryFn: () => advisorApi.getConversation(activeConvId!).then((r) => r.data),
    enabled: !!activeConvId,
  });

  // Sync messages from loaded conversation
  useEffect(() => {
    if (activeConv?.messages) {
      setLocalMessages(activeConv.messages);
    }
  }, [activeConv]);

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [localMessages]);

  const chatMutation = useMutation({
    mutationFn: ({ msg, convId }: { msg: string; convId: number | null }) =>
      advisorApi.chat(msg, selectedPortfolio, convId ?? undefined),
    onMutate: ({ msg }) => {
      // Optimistically add user message
      const optimistic: ChatMessage = {
        id: Date.now(),
        role: "user",
        content: msg,
        created_at: new Date().toISOString(),
      };
      setLocalMessages((prev) => [...prev, optimistic]);
    },
    onSuccess: (res, { convId }) => {
      const newConvId = res.data.conversation_id;
      if (!convId) setActiveConvId(newConvId);
      const aiMsg: ChatMessage = {
        id: Date.now() + 1,
        role: "assistant",
        content: res.data.response,
        created_at: new Date().toISOString(),
      };
      setLocalMessages((prev) => [...prev, aiMsg]);
      qc.invalidateQueries({ queryKey: ["conversations"] });
      if (activeConvId) qc.invalidateQueries({ queryKey: ["conversation", activeConvId] });
    },
    onError: () => toast.error("Failed to get response"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => advisorApi.deleteConversation(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      if (activeConvId === id) {
        setActiveConvId(null);
        setLocalMessages([]);
      }
      toast.success("Conversation deleted");
    },
  });

  function handleSend() {
    const msg = message.trim();
    if (!msg || chatMutation.isPending) return;
    setMessage("");
    chatMutation.mutate({ msg, convId: activeConvId });
  }

  function handleNewChat() {
    setActiveConvId(null);
    setLocalMessages([]);
    setMessage("");
  }

  function handleSelectConv(conv: Conversation) {
    setActiveConvId(conv.id);
    setLocalMessages([]);
  }

  const handleReview = async () => {
    if (!selectedPortfolio) return;
    const msg = "Please give me a full portfolio health review.";
    setMessage("");
    chatMutation.mutate({ msg, convId: activeConvId });
  };

  const isLoading = chatMutation.isPending;

  return (
    <div className="flex h-full gap-0 -m-6">
      {/* Sidebar — conversation history */}
      <aside className="w-64 shrink-0 border-r border-border flex flex-col bg-card">
        <div className="p-3 border-b border-border">
          <button
            onClick={handleNewChat}
            className="w-full px-3 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90"
          >
            + New Chat
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {conversations.length === 0 && (
            <p className="text-xs text-muted-foreground px-2 py-4 text-center">No saved chats yet</p>
          )}
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`group flex items-start gap-1 rounded-md px-2 py-2 cursor-pointer transition-colors ${
                activeConvId === conv.id
                  ? "bg-secondary text-foreground"
                  : "hover:bg-secondary/50 text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => handleSelectConv(conv)}
            >
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium truncate">{conv.title}</p>
                <p className="text-xs opacity-50 mt-0.5">
                  {new Date(conv.updated_at).toLocaleDateString()}
                </p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(conv.id); }}
                className="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-300 p-0.5 rounded shrink-0 transition-opacity"
                title="Delete"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-border shrink-0">
          <select
            value={selectedPortfolio ?? ""}
            onChange={(e) => setSelectedPortfolio(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-md border border-border bg-input px-3 py-1.5 text-sm"
          >
            <option value="">No portfolio context</option>
            {portfolios.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          {selectedPortfolio && (
            <button
              onClick={handleReview}
              disabled={isLoading}
              className="px-3 py-1.5 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 disabled:opacity-50"
            >
              Full Portfolio Review
            </button>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {localMessages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground">
              <p className="text-lg font-semibold mb-1">Ask Jarvis</p>
              <p className="text-sm">
                Ask anything about your portfolio or the market.<br />
                Select a portfolio above for context-aware answers.
              </p>
            </div>
          )}
          {localMessages.map((msg, i) => (
            <MessageBubble key={msg.id ?? i} msg={msg} />
          ))}
          {isLoading && (
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center text-xs shrink-0">J</div>
              <div className="rounded-xl bg-secondary px-4 py-2 text-sm text-muted-foreground animate-pulse">
                Thinking…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-5 py-4 border-t border-border shrink-0">
          <div className="flex gap-2 items-end">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSend(); }}
              placeholder="Ask something… (⌘↵ to send)"
              rows={2}
              className="flex-1 rounded-md border border-border bg-input px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <button
              onClick={handleSend}
              disabled={isLoading || !message.trim()}
              className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
        isUser ? "bg-indigo-700 text-white" : "bg-primary/20 text-primary"
      }`}>
        {isUser ? "Y" : "J"}
      </div>
      <div className={`max-w-[75%] rounded-xl px-4 py-2.5 text-sm ${
        isUser ? "bg-indigo-700 text-white" : "bg-secondary text-foreground"
      }`}>
        <div
          className="prose prose-sm max-w-none prose-invert"
          dangerouslySetInnerHTML={{ __html: markdownToHtml(msg.content) }}
        />
      </div>
    </div>
  );
}

function markdownToHtml(md: string): string {
  return md
    .replace(/^### (.+)$/gm, "<h3 class='text-sm font-semibold mt-2 mb-1'>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2 class='text-sm font-bold mt-3 mb-1'>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1 class='text-base font-bold mt-3 mb-1'>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code class='bg-black/20 px-1 rounded text-xs'>$1</code>")
    .replace(/^- (.+)$/gm, "<li class='ml-4'>$1</li>")
    .replace(/(<li.*<\/li>\n?)+/g, "<ul class='list-disc my-1'>$&</ul>")
    .replace(/\n\n/g, "<br/><br/>")
    .replace(/\n/g, "<br/>");
}
