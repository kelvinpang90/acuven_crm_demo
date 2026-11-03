import api from './api';
import type { ApiResponse, PaginatedResponse, Message, Conversation, WhatsAppTemplate } from '@/types';

export interface MessageListParams {
  channel?: string;
  is_read?: boolean;
  contact_id?: string;
  page?: number;
  page_size?: number;
}

export type ConversationSort = 'time' | 'urgency';

export interface ConversationListParams {
  channel?: string;
  unread?: boolean;
  // `urgency` only takes effect together with `unread=true`.
  sort?: ConversationSort;
  page?: number;
  page_size?: number;
}

export const messagesApi = {
  getMessages: (params: MessageListParams) =>
    api.get<ApiResponse<PaginatedResponse<Message>>>('/messages', { params }),

  getConversations: (params: ConversationListParams) =>
    api.get<ApiResponse<PaginatedResponse<Conversation>>>('/messages/conversations', { params }),

  markContactRead: (contactId: string) =>
    api.patch<ApiResponse<{ updated: number }>>(`/messages/contact/${contactId}/read`),

  // The latest `limit` messages, or the `limit` older than message `before`; oldest first.
  getContactMessages: (contactId: string, params?: { before?: string; limit?: number }) =>
    api.get<ApiResponse<Message[]>>(`/messages/contact/${contactId}`, { params }),

  markRead: (id: string) =>
    api.patch<ApiResponse<Message>>(`/messages/${id}/read`),

  sendWhatsApp: (contactId: string, message: string) =>
    api.post<ApiResponse<Message>>(
      '/messages/whatsapp/send',
      { contact_id: contactId, message },
      { timeout: 45000 },
    ),

  // Read afresh each time the template dialog opens.
  getWhatsAppTemplates: () =>
    api.get<ApiResponse<WhatsAppTemplate[]>>('/messages/whatsapp/templates'),

  sendWhatsAppTemplate: (contactId: string, templateName: string, language: string, params: string[]) =>
    api.post<ApiResponse<Message>>(
      '/messages/whatsapp/send-template',
      { contact_id: contactId, template_name: templateName, language, params },
      { timeout: 45000 },
    ),

  retryWhatsApp: (messageId: string) =>
    api.post<ApiResponse<Message>>(`/messages/${messageId}/whatsapp/retry`, undefined, { timeout: 45000 }),

  sendEmail: (contactId: string, subject: string, body: string) =>
    api.post<ApiResponse<Message>>('/messages/email/send', { contact_id: contactId, subject, body }),
};
