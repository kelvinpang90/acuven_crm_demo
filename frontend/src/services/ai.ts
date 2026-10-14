import api from './api';
import type { AiConversationBrief, AiStatus, ApiResponse } from '@/types';

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
};
