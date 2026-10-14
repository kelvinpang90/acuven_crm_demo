import api from './api';
import type { AiConversationBrief, AiLanguage, AiReplyDraft, AiStatus, AiTranslation, ApiResponse } from '@/types';

let aiStatusPromise: Promise<AiStatus> | null = null;

export const aiApi = {
  getStatus: () => {
    if (!aiStatusPromise) {
      aiStatusPromise = api.get<ApiResponse<AiStatus>>('/ai/status').then((response) => response.data.data);
    }
    return aiStatusPromise;
  },

  createConversationBrief: (contactId: string, outputLanguage?: 'zh-CN' | 'en') =>
    api.post<ApiResponse<AiConversationBrief>>(
      `/ai/conversations/${contactId}/brief`,
      outputLanguage ? { output_language: outputLanguage } : {},
      { timeout: 58000 },
    ),

  createReplyDraft: (contactId: string, channel: 'whatsapp' | 'email', instruction: string | undefined, outputLanguage: AiLanguage) =>
    api.post<ApiResponse<AiReplyDraft>>(`/ai/conversations/${contactId}/reply-draft`, { channel, ...(instruction ? { instruction } : {}), output_language: outputLanguage }, { timeout: 58000 }),

  translate: (contactId: string, text: string, targetLanguage: AiLanguage) =>
    api.post<ApiResponse<AiTranslation>>('/ai/translate', { contact_id: contactId, text, target_language: targetLanguage }, { timeout: 58000 }),
};
