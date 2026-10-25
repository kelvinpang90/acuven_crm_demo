import api from './api';
import type { AiContactExtract, AiContactSummary,AiConversationBrief, AiDashboardInsight, AiDealNextStep, AiFollowUpDraft, AiImportColumn, AiImportColumns, AiLanguage, AiReplyDraft, AiStatus, AiTranslation, ApiResponse } from '@/types';

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

  createContactSummary: (contactId: string, outputLanguage?: 'zh-CN' | 'en') =>
    api.post<ApiResponse<AiContactSummary>>(
      `/ai/contacts/${contactId}/summary`,
      outputLanguage ? { output_language: outputLanguage } : {},
      { timeout: 58000 },
    ),

  createContactExtract: (contactId: string, outputLanguage?: 'zh-CN' | 'en') =>
    api.post<ApiResponse<AiContactExtract>>(
      `/ai/contacts/${contactId}/extract`,
      outputLanguage ? { output_language: outputLanguage } : {},
      { timeout: 58000 },
    ),

  createDealNextStep: (dealId: string, outputLanguage?: 'zh-CN' | 'en') =>
    api.post<ApiResponse<AiDealNextStep>>(
      `/ai/deals/${dealId}/next-step`,
      outputLanguage ? { output_language: outputLanguage } : {},
      { timeout: 58000 },
    ),

  createDashboardInsight: (outputLanguage?: 'zh-CN' | 'en') =>
    api.post<ApiResponse<AiDashboardInsight>>(
      '/ai/dashboard/insight',
      outputLanguage ? { output_language: outputLanguage } : {},
      { timeout: 58000 },
    ),

  createFollowUpDraft: (
    contactId: string,
    data: { deal_id: string; note: string; today: string; output_language?: 'zh-CN' | 'en' },
  ) => api.post<ApiResponse<AiFollowUpDraft>>(`/ai/contacts/${contactId}/followup-draft`, data, { timeout: 58000 }),

  createReplyDraft: (contactId: string, channel: 'whatsapp' | 'email', instruction: string | undefined, outputLanguage: AiLanguage) =>
    api.post<ApiResponse<AiReplyDraft>>(`/ai/conversations/${contactId}/reply-draft`, { channel, ...(instruction ? { instruction } : {}), output_language: outputLanguage }, { timeout: 58000 }),

  suggestImportColumns: (columns: AiImportColumn[], outputLanguage?: 'zh-CN' | 'en') =>
    api.post<ApiResponse<AiImportColumns>>(
      '/ai/import/columns',
      { columns, ...(outputLanguage ? { output_language: outputLanguage } : {}) },
      { timeout: 58000 },
    ),

  translate: (contactId: string, text: string, targetLanguage: AiLanguage) =>
    api.post<ApiResponse<AiTranslation>>('/ai/translate', { contact_id: contactId, text, target_language: targetLanguage }, { timeout: 58000 }),
};
