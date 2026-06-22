/**
 * Kee API client — REST + WebSocket. All UIs in this dashboard go through
 * here so swapping the backend (e.g. proxy in dev) is a one-line change.
 */

const RAW_BASE = (import.meta as any).env?.VITE_KEE_API ?? 'http://127.0.0.1:7330';
export const API_BASE = RAW_BASE.replace(/\/$/, '');

async function get<T = any>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) {
        throw new Error(`GET ${path} → ${res.status}: ${(await res.text()).slice(0, 200)}`);
    }
    return res.json();
}

async function post<T = any>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    if (!res.ok) {
        throw new Error(`POST ${path} → ${res.status}: ${(await res.text()).slice(0, 200)}`);
    }
    return res.json();
}

// ── Endpoints ─────────────────────────────────────────────────────────────

export const api = {
    health: () => get<{ status: string; model: string; tools: number; uptime_s: number }>('/health'),
    tools: () => get<{ count: number; tools: ToolInfo[] }>('/tools'),
    toolSource: (name: string) =>
        get<{ name: string; path: string; lines: number; bytes: number; source: string }>(`/tools/${name}/source`),
    toolExecute: (name: string, args: Record<string, any>) =>
        post<{ ok: boolean; result?: any; error?: string; elapsed_ms: number }>(`/tools/${name}/execute`, { arguments: args }),
    systemDaemons: () => get<{ rows: DaemonRow[] }>('/system/daemons'),
    systemLogs: (name: string, tail = 200) => get<{ name: string; path: string; exists: boolean; lines: string[] }>(`/system/logs/${name}?tail=${tail}`),
    systemSupervisor: () => get<SupervisorState>('/system/supervisor'),
    voiceConfig: () => get<VoicePrefs>('/voice/config'),
    voiceConfigSet: (patch: Partial<VoicePrefs>) =>
        post<{ ok: boolean; config: VoicePrefs }>('/voice/config', patch),
    voiceVoices: () => get<{ voices: InstalledVoice[] }>('/voice/voices'),
    voiceCatalog: () => get<{ voices: CatalogVoice[] }>('/voice/catalog'),
    voiceInstall: (stems: string[]) =>
        post<{ results: { ok: boolean; stem: string; size_mb?: number; error?: string; already_installed?: boolean }[] }>('/voice/install', { stems }),
    voiceUninstall: (stem: string) =>
        post<{ ok: boolean; stem: string; removed_files?: number; error?: string }>(`/voice/voices/${stem}/uninstall`, {}),
    voiceSpeak: (text: string, voice?: string) =>
        post<{ ok: boolean; voice: string; elapsed_ms: number; wav_bytes: number }>('/voice/speak', { text, voice, play: true }),
    audit: (limit = 50) => get<{ rows: AuditRow[] }>(`/audit?limit=${limit}`),
    anomalies: (limit = 50) => get<{ rows: AnomalyRow[] }>(`/anomalies?limit=${limit}`),
    heartbeats: (n = 10) => get<{ count: number; rows: HeartbeatRow[] }>(`/heartbeat/recent?n=${n}`),
    conversations: (limit = 20) => get<{ rows: ConversationRow[] }>(`/conversations?limit=${limit}`),
    conversationDetail: (id: string) =>
        get<{ id: string; messages: MessageRow[] }>(`/conversations/${id}`),
    goals: () => get<{ goals: GoalRow[] }>('/goals'),
    goalsRaw: () => get<{ markdown: string; exists: boolean }>('/goals/raw'),
    putGoalsRaw: async (markdown: string) => {
        const res = await fetch(`${API_BASE}/goals/raw`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ markdown }),
        });
        return res.json();
    },
    worldEntities: (type?: string) =>
        get<{ entities: EntityRow[] }>('/world-model/entities' + (type ? `?type=${type}` : '')),
    worldRelations: () => get<{ edges: WorldEdgeRow[] }>('/world-model/relations'),
    worldImpact: (id: string, max_depth = 3) =>
        get<ImpactRow>(`/world-model/impact/${id}?max_depth=${max_depth}`),
    economySummary: (window_days?: number) =>
        get<EconomySummary>(
            '/economy/summary' + (window_days ? `?window_days=${window_days}` : '')
        ),
    economyRecent: (n = 20) => get<{ entries: EconomyRow[] }>(`/economy/recent?n=${n}`),
    autonomy: (window = 50) => get<AutonomySummary>(`/autonomy/summary?window=${window}`),
    digestToday: () => get<{ date: string; markdown: string }>('/digest/today'),
    cycleState: () => get<any>('/cycle/state'),
    voiceState: () => get<any>('/voice/state'),
    cycleRun: () => post<{ ok: boolean }>('/cycle/run', {}),
    cycleProposals: () => get<{ proposals: { date: string; path: string; bytes: number; applied: boolean }[] }>('/cycle/proposals'),
    cycleApplyProposal: (date: string) =>
        post<{ ok: boolean; soul_bytes_added?: number; git_committed?: boolean; patch_preview?: string }>(`/cycle/proposals/${date}/apply`, {}),
    vaultList: (subdir = '') => get<{ items: VaultItem[] }>(`/vault/list${subdir ? '?subdir=' + encodeURIComponent(subdir) : ''}`),
    vaultRead: (path: string) => get<{ path: string; bytes: number; lines: number; content: string }>(`/vault/read?path=${encodeURIComponent(path)}`),
    vaultWrite: async (path: string, content: string) => {
        const res = await fetch(`${API_BASE}/vault/write?path=${encodeURIComponent(path)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        return res.json();
    },
    proposals: () => get<{ proposals: ProposalRow[] }>('/proposals'),
    proposalDetail: (date: string) => get<{ date: string; markdown: string }>(`/proposals/${date}`),

    chat: (message: string, session_id = 'dashboard') =>
        post<ChatResponse>('/chat', { message, session_id }),
    resetChat: (session_id = 'dashboard') => post(`/chat/${session_id}/reset`, {}),
    chatActive: (session_id = 'dashboard') =>
        get<{ session_id: string; conversation_id: string | null; messages: { role: string; content: string; tool_name?: string | null }[]; active_in_memory: boolean }>(`/chat/${session_id}/active`),

    /**
     * Stream a chat response. Calls onDelta(chunk) for each token-ish piece
     * and onDone({conversation_id}) at the end. Returns an AbortController.
     */
    chatStream(
        message: string,
        opts: {
            session_id?: string;
            onDelta?: (text: string) => void;
            onReplace?: (text: string) => void;
            onDone?: (info: { conversation_id?: string }) => void;
            onError?: (err: string) => void;
        }
    ): AbortController {
        const ctrl = new AbortController();
        (async () => {
            try {
                const res = await fetch(`${API_BASE}/chat/stream`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message, session_id: opts.session_id ?? 'dashboard' }),
                    signal: ctrl.signal,
                });
                if (!res.ok || !res.body) {
                    opts.onError?.(`HTTP ${res.status}`);
                    return;
                }
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buf = '';
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buf += decoder.decode(value, { stream: true });
                    // Parse SSE events: "data: <json>\n\n"
                    let idx;
                    while ((idx = buf.indexOf('\n\n')) >= 0) {
                        const raw = buf.slice(0, idx).trim();
                        buf = buf.slice(idx + 2);
                        if (!raw.startsWith('data:')) continue;
                        try {
                            const obj = JSON.parse(raw.slice(5).trim());
                            if (obj.type === 'delta' && opts.onDelta) opts.onDelta(obj.text);
                            else if (obj.type === 'replace' && opts.onReplace) opts.onReplace(obj.text);
                            else if (obj.type === 'done' && opts.onDone) opts.onDone(obj);
                            else if (obj.type === 'error' && opts.onError) opts.onError(obj.error);
                        } catch {/* ignore parse */}
                    }
                }
            } catch (e: any) {
                if (e.name !== 'AbortError') opts.onError?.(String(e));
            }
        })();
        return ctrl;
    },

    // File attachments per chat session
    attachFile: async (file: File, session_id = 'dashboard'): Promise<AttachResponse> => {
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${API_BASE}/chat/${session_id}/attach`, {
            method: 'POST',
            body: fd,
        });
        if (!res.ok) throw new Error(`attach ${res.status}: ${(await res.text()).slice(0, 200)}`);
        return res.json();
    },
    listAttachments: (session_id = 'dashboard') =>
        get<{ session_id: string; items: AttachmentItem[] }>(`/chat/${session_id}/attachments`),
    deleteAttachment: async (filename: string, session_id = 'dashboard') => {
        const res = await fetch(
            `${API_BASE}/chat/${session_id}/attachments/${encodeURIComponent(filename)}`,
            { method: 'DELETE' }
        );
        return res.ok;
    },

    // Cross-conversation memory
    recentSummaries: (limit = 10) => get<{ rows: ConversationSummaryRow[] }>(`/memory/recent_summaries?limit=${limit}`),

    // Provider chain + cost
    llmProviders: () => get<LLMProvidersResponse>('/llm/providers'),
    llmCost: () => get<LLMCostResponse>('/llm/cost'),
    llmRecent: (limit = 30) => get<{ rows: LLMCallRow[] }>(`/llm/recent?limit=${limit}`),
    routerConfig: () => get<RouterConfigResponse>('/router/config'),
    keecodeStatus: () => get<KeeCodeStatus>('/keecode/status'),
    keecodeLaunch: (body: { prompt?: string; workdir?: string; model?: string }) =>
        post<KeeCodeLaunchResponse>('/keecode/launch', body),
    keecodeContext: (body: { notes?: string; session_id?: string }) =>
        post<KeeCodeStatus & { ok: boolean; context_path: string }>('/keecode/context', body),
    updateSettings: (body: {
        daily_cap_usd?: number;
        primary?: string;
        model?: string;
        code_agent?: string;
        code_agent_model?: string;
        opencode_command?: string;
        opencode_repo?: string;
    }) =>
        post<{ ok: boolean; changed: string[]; chain_rebuilt?: boolean }>('/settings', body),

    // Conversation history & summarization
    summarizeOne: (conversation_id: string) =>
        post<{ ok: boolean; summary: string | null }>(`/memory/summarize/${conversation_id}`, {}),

    // Notifications
    notifications: (opts: { direction?: string; source?: string; handled?: boolean; limit?: number } = {}) => {
        const q = new URLSearchParams();
        if (opts.direction) q.set('direction', opts.direction);
        if (opts.source) q.set('source', opts.source);
        if (opts.handled !== undefined) q.set('handled', String(opts.handled));
        if (opts.limit) q.set('limit', String(opts.limit));
        return get<{ rows: NotificationRow[] }>(`/notifications?${q.toString()}`);
    },
    notificationsUnreadCount: () => get<{ count: number }>('/notifications/unread_count'),
    notificationsMarkHandled: (id: number) => post(`/notifications/${id}/handled`, {}),
    notificationsHandleAll: () => post('/notifications/handle_all', {}),
    notificationsInbound: (body: { source: string; title?: string; body: string; urgency?: number }) =>
        post('/notifications/inbound', body),

    // Agent control
    rebuildAgent: () => post<{ ok: boolean; chain_providers: string[]; primary: string }>('/agent/rebuild', {}),
    testProvider: (name: string) =>
        post<{ ok: boolean; healthy?: boolean; latency_ms?: number; error?: string }>(`/llm/test_provider/${name}`, {}),
    putRouterConfig: async (body: { direct_rules?: { match: string; reply: string }[]; tier_hints?: Record<string, string[]> }) => {
        const res = await fetch(`${API_BASE}/router/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        return res.json();
    },
};

// ── WebSocket /stream ─────────────────────────────────────────────────────

export type StreamEvent = {
    type: string;
    ts?: string;
    [k: string]: unknown;
};

export function openStream(onEvent: (e: StreamEvent) => void): () => void {
    // ws:// or wss:// based on http(s)
    const wsUrl = API_BASE.replace(/^http/i, 'ws') + '/stream';
    let ws: WebSocket | null = new WebSocket(wsUrl);
    let closed = false;

    ws.onmessage = (msg) => {
        try {
            onEvent(JSON.parse(msg.data));
        } catch {
            /* ignore parse errors */
        }
    };
    ws.onclose = () => {
        if (closed) return;
        // Reconnect after 2s
        setTimeout(() => {
            if (!closed) ws = openStreamRaw(onEvent, () => (ws = null));
        }, 2000);
    };

    return () => {
        closed = true;
        ws?.close();
    };
}

function openStreamRaw(onEvent: (e: StreamEvent) => void, onClose: () => void): WebSocket {
    const wsUrl = API_BASE.replace(/^http/i, 'ws') + '/stream';
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (msg) => {
        try {
            onEvent(JSON.parse(msg.data));
        } catch {
            /* ignore */
        }
    };
    ws.onclose = onClose;
    return ws;
}

// ── Type definitions ──────────────────────────────────────────────────────

export interface ToolInfo {
    name: string;
    risk_level: number;
    source: string;
    description: string;
    parameters_schema: unknown;
}
export interface AuditRow {
    id: number;
    timestamp: string;
    action: string;
    tool_name: string;
    parameters?: string;
    result?: string;
    risk_level: number;
    success: number | boolean;
    error?: string | null;
}
export interface AnomalyRow {
    id: number;
    timestamp: string;
    audit_id: number | null;
    tool_name: string;
    kind: string;
    detail: string | null;
    severity: number;
}
export interface HeartbeatRow {
    id: number;
    timestamp: string;
    mode: string;
    checks: Record<string, any>;
}
export interface ConversationRow {
    id: string;
    source: string;
    started_at: string;
    last_active: string;
    summary: string | null;
    token_count: number;
}
export interface MessageRow {
    role: string;
    content: string;
    tool_name?: string | null;
}
export interface GoalRow {
    title: string;
    status: string;
    deadline: string | null;
    days_left: number | null;
    project: string | null;
    progress_pct: number | null;
    notes: string[];
    extras: Record<string, string>;
}
export interface EntityRow {
    id: string;
    name: string;
    type: string;
    state: Record<string, any>;
    criticality: number;
    notes: string | null;
    updated_at: string | null;
}
export interface WorldEdgeRow {
    source: string;
    target: string;
    relation: string;
    weight: number;
    description: string | null;
}
export interface ImpactRow {
    entity_id: string;
    score: number;
    recommendation: string;
    affected_count: number;
    affected: any[];
}
export interface EconomyRow {
    id: number;
    timestamp: string;
    tool_name: string;
    cost_usd: number;
    model: string | null;
    duration_ms: number | null;
    tokens_in: number | null;
    tokens_out: number | null;
    task_summary: string | null;
}
export interface EconomySummary {
    window_days: number | null;
    total_calls: number;
    total_spent_usd: number;
    by_tool: { tool: string; calls: number; spent_usd: number }[];
    by_model: { model: string; calls: number; spent_usd: number }[];
}
export interface AutonomySummary {
    tool_count: number;
    tools: {
        tool_name: string;
        samples: number;
        success_rate: number | null;
        recent_corrections: number;
    }[];
}
export interface ProposalRow {
    date: string;
    path: string;
    size: number;
}
export interface ChatResponse {
    response: string;
    conversation_id: string;
    iteration: number;
}
export interface LLMProviderInfo {
    name: string;
    model: string;
    cost_in_per_mtok: number;
    cost_out_per_mtok: number;
    healthy: boolean;
    is_primary: boolean;
}
export interface LLMProvidersResponse {
    primary: string | null;
    providers: LLMProviderInfo[];
}
export interface LLMCostStatus {
    today_usd: number;
    cap_usd: number;
    pct_of_cap: number;
    near_cap: boolean;
    kill_active: boolean;
}
export interface LLMCostResponse {
    today: LLMCostStatus;
    by_provider: Record<string, { calls: number; cost_usd: number; tokens_in: number; tokens_out: number }>;
}
export interface LLMCallRow {
    id: number;
    timestamp: string;
    provider: string;
    model: string;
    tier: string;
    latency_ms: number | null;
    tokens_in: number | null;
    tokens_out: number | null;
    cost_usd: number | null;
    success: boolean;
}
export interface RouterConfigResponse {
    direct_rules: { pattern: string; reply: string }[];
    tier_hints: Record<string, string[]>;
}
export interface KeeCodeStatus {
    ok: boolean;
    agent: string;
    model: string;
    opencode_repo: string;
    opencode_repo_exists: boolean;
    opencode_command: string;
    opencode_command_resolved: string | null;
    opencode_command_source: string;
    npx_available: boolean;
    bun_available: boolean;
    config_path: string;
    config_exists: boolean;
    context_path: string;
    context_exists: boolean;
    data_dir: string;
    ollama_host: string;
    model_id: string;
    hint: string | null;
}
export interface KeeCodeLaunchResponse {
    ok: boolean;
    pid?: number;
    script?: string;
    workdir?: string;
    model?: string;
    context_path?: string;
    error?: string;
}
export interface AttachmentItem {
    path: string;
    name: string;
    bytes: number;
}
export interface AttachResponse {
    ok: boolean;
    filename: string;
    path: string;
    bytes: number;
    session_attached_count: number;
}
export interface ConversationSummaryRow {
    id: string;
    source: string;
    last_active: string;
    summary: string;
}
export interface VaultItem {
    path: string;
    name: string;
    bytes: number;
    mtime: number;
}
export interface DaemonRow {
    pid: number;
    surface: string;
    started_at: number;
    cpu_pct: number;
    rss_mb: number;
    cmd: string;
}
export interface SupervisedSurface {
    name: string;
    enabled: boolean;
    alive: boolean;
    pid: number | null;
    started_at: number;
    uptime_s: number;
    restarts: number;
    last_exit_code: number | null;
    last_exit_at: number;
    backoff_s: number;
    log_path: string;
    description: string;
}
export interface VoicePrefs {
    voice: string;
    length_scale: number;
    noise_scale: number;
    noise_w: number;
    speak_responses: boolean;
    sentence_silence_s: number;
    voice_per_lang: Record<string, string>;
    auto_detect_language: boolean;
    stt_language: string;
}
export interface InstalledVoice {
    name: string;
    path: string;
    size_mb: number;
    language: string;
    sample_rate: number | null;
    has_metadata: boolean;
}
export interface CatalogVoice {
    stem: string;
    locale: string;
    name: string;
    quality: string;
    description: string;
    approx_mb: number;
    installed: boolean;
}
export interface SupervisorState {
    running: boolean;
    supervisor_pid?: number;
    updated_at?: number;
    stale_s?: number;
    error?: string;
    surfaces: SupervisedSurface[];
}
export interface NotificationRow {
    id: number;
    timestamp: string;
    direction: 'inbound' | 'outbound';
    source: string;
    title: string | null;
    body: string;
    urgency: number;   // 0 low, 1 normal, 2 critical
    handled: boolean;
    metadata: string | null;
}
