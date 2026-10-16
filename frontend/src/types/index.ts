// === User ===
export interface User {
  id: string;
  name: string;
  email: string;
  role: 'admin' | 'manager' | 'sales';
  avatar_url: string | null;
  language: string;
  manager_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// === Contact ===
export type DealStatus = 'lead' | 'following' | 'negotiating' | 'won' | 'lost';
export type Priority = 'high' | 'mid' | 'low';

export interface Contact {
  id: string;
  name: string;
  company: string | null;
  industry: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  notes: string | null;
  assigned_to: string | null;
  assigned_to_name?: string | null;
  last_contact: string | null;
  tags: string[] | null;
  is_archived: number;
  autocount_customer_code: string | null;
  total_deal_amount: number;
  deal_count: number;
  wa_handover_active: boolean;
  wa_handover_expires_at: string | null;
  wa_handover_updated_at: string | null;
  wa_reply_window_until: string | null;
  phone_conflict: boolean;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

// === AutoCount ===
export type AutocountDocType = 'invoice' | 'quotation';

export interface AutocountLineItem {
  product_code: string | null;
  description: string | null;
  qty: number | null;
  unit: string | null;
  unit_price: number | null;
  discount_amt: number | null;
  sub_total: number | null;
}

export interface AutocountDocument {
  id: string;
  doc_type: AutocountDocType;
  doc_no: string;
  customer_code: string;
  contact_id: string | null;
  status: string;
  total_amount: number;
  currency_code: string;
  doc_date: string | null;
  outstanding_amount: number | null;
  due_date: string | null;
  validity: string | null;
  line_items: AutocountLineItem[];
  synced_at: string;
}

export interface AutocountSyncResult {
  customers_synced: number;
  invoices_synced: number;
  invoices_unmatched: number;
  quotations_synced: number;
  quotations_unmatched: number;
  synced_at: string;
}

// === Deal ===
export interface Deal {
  id: string;
  contact_id: string;
  contact_name?: string | null;
  contact_company?: string | null;
  title: string | null;
  status: DealStatus;
  priority: Priority;
  amount: number;
  assigned_to: string | null;
  assigned_to_name?: string | null;
  won_at: string | null;
  created_at: string;
  updated_at: string;
}

// === Activity ===
export type ActivityType = 'phone' | 'email' | 'meeting' | 'WhatsApp' | 'other' | 'status change';

export interface Activity {
  id: string;
  contact_id: string;
  deal_id: string;
  user_id: string;
  type: ActivityType;
  content: string | null;
  follow_date: string;
  created_at: string;
  user_name?: string;
}

// === Task ===
export interface Task {
  id: string;
  title: string;
  contact_id: string | null;
  assigned_to: string | null;
  priority: Priority;
  due_date: string | null;
  is_done: boolean;
  done_at: string | null;
  created_at: string;
  updated_at: string;
  contact_name?: string;
  assigned_to_name?: string;
}

// === Message ===
export type MessageChannel = 'whatsapp' | 'email';
export type MessageDirection = 'inbound' | 'outbound';
export type MessageAuthorKind = 'customer' | 'ai' | 'console' | 'agent';
export type MessageDeliveryStatus = 'sent' | 'delivered' | 'read' | 'failed';

export interface Message {
  id: string;
  contact_id: string | null;
  channel: MessageChannel;
  direction: MessageDirection;
  sender_id: string;
  recipient_id: string;
  subject: string | null;
  body: string;
  external_id: string | null;
  is_read: boolean;
  assigned_to: string | null;
  author_kind: MessageAuthorKind | null;
  bot_id: string | null;
  delivery_status: MessageDeliveryStatus | null;
  delivery_error: string | null;
  created_at: string;
  contact_name?: string;
  sent_by: string | null;
  sent_by_name: string | null;
}

// === Routing Rule ===
export type RoutingStrategy = 'workload' | 'region' | 'win_rate';

export interface RoutingRule {
  id: string;
  name: string;
  is_active: boolean;
  priority: number;
  strategy: RoutingStrategy;
  conditions: Record<string, unknown> | null;
  target_users: string[] | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// === Sales Target ===
export interface SalesTarget {
  id: string;
  user_id: string;
  year: number;
  month: number;
  target_amount: number;
  target_count: number;
  created_at: string;
  updated_at: string;
}

// === Project Tracking (self-contained module) ===
// Data-independent from the CRM core: own entities, own mock service layer.
export type ProjectAlertLevel = 'normal' | 'watch' | 'urgent' | 'done';

export interface ProjectStepHistory {
  id: string;
  project_id: string;
  step_no: number; // 1-12
  entered_at: string; // ISO datetime the project entered this step
  updated_by: string; // project manager name
  note: string | null;
  photos: string[]; // file labels, used by steps 3 (inspection) & 10 (completion)
}

export interface Project {
  id: string;
  customer_name: string;
  address: string;
  service_type: string;
  project_manager: string;
  current_step: number; // 1-12
  created_at: string;
  last_updated_at: string;
  // Warranty confirmation (step 12 gate): set once when the project first
  // advances into warranty_active; null for projects that reached step 12
  // before this feature existed.
  satisfaction_score: number | null; // 1-10
  customer_feedback: string | null;
  signature_data: string | null; // base64 PNG data URI
  signed_at: string | null;
  history: ProjectStepHistory[];
}

// Editable fields for create/update (matches backend ProjectCreate/Update).
export interface ProjectInput {
  customer_name: string;
  address: string;
  service_type: string;
  project_manager: string;
  current_step: number;
}

// === API Response ===
export interface ApiResponse<T = unknown> {
  success: boolean;
  data: T;
  message: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiError {
  success: false;
  error: {
    code: string;
    message: string;
    fields?: Record<string, string | null> | null;
  };
}

// === AI Assistance ===
export interface AiStatus {
  enabled: boolean;
}

export interface AiConversationBrief {
  request_id: string | null;
  customer_need: string;
  conversation_state: string;
  customer_mood: 'calm' | 'neutral' | 'frustrated' | 'urgent';
  open_questions: string[];
  suggested_next_step: string;
  truncated: boolean;
}

export interface AiContactSummary {
  request_id: string | null;
  summary: string;
  suggested_next_step: string;
  truncated: boolean;
}

export interface AiDealNextStep {
  request_id: string | null;
  assessment: string;
  stall_reason: string;
  next_steps: string[];
  idle_days: number;
  days_in_status: number;
  stalled: boolean;
  truncated: boolean;
}

export type AiLanguage = 'zh-CN' | 'en' | 'ms';

export interface AiReplyDraft {
  request_id: string | null;
  draft: string;
  subject: string | null;
  note_for_agent: string;
}

export interface AiTranslation {
  request_id: string | null;
  translation: string;
  detected_language: AiLanguage | 'other';
}

export interface AiFollowUpTaskDraft {
  title: string;
  due_date: string | null;
  priority: Priority;
}

export interface AiFollowUpDraft {
  request_id: string | null;
  activity_type: Exclude<ActivityType, 'status change'>;
  content: string;
  tasks: AiFollowUpTaskDraft[];
}
