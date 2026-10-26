import api from './api';
import type {
  ApiResponse,
  PaginatedResponse,
  Contact,
  Activity,
  ContactImportResult,
  DuplicateCheckRequest,
  DuplicateResult,
} from '@/types';

export interface WhatsAppHandover {
  wa_handover_active: boolean;
  wa_handover_expires_at: string | null;
  wa_handover_updated_at: string | null;
}

export interface ContactListParams {
  search?: string;
  industry?: string;
  assigned_to?: string;
  page?: number;
  page_size?: number;
  sort_by?: string;
  order?: string;
  is_archived?: number;
  unassigned?: number;
}

export const contactsApi = {
  getContacts: (params: ContactListParams) =>
    api.get<ApiResponse<PaginatedResponse<Contact>>>('/contacts', { params }),

  getContact: (id: string) =>
    api.get<ApiResponse<Contact>>(`/contacts/${id}`),

  handbackWhatsApp: (id: string) =>
    api.post<ApiResponse<WhatsAppHandover>>(`/contacts/${id}/whatsapp/handback`),

  checkDuplicates: (data: DuplicateCheckRequest) =>
    api.post<ApiResponse<DuplicateResult>>('/contacts/duplicate-check', data),

  getDuplicates: (id: string) =>
    api.get<ApiResponse<DuplicateResult>>(`/contacts/${id}/duplicates`),

  createContact: (data: Partial<Contact>) =>
    api.post<ApiResponse<Contact>>('/contacts', data),

  updateContact: (id: string, data: Partial<Contact>) =>
    api.put<ApiResponse<Contact>>(`/contacts/${id}`, data),

  deleteContact: (id: string) =>
    api.delete<ApiResponse<null>>(`/contacts/${id}`),

  archiveContact: (id: string, is_archived: boolean) =>
    api.patch<ApiResponse<Contact>>(`/contacts/${id}/archive`, { is_archived: is_archived ? 1 : 0 }),

  importContacts: (
    file: File,
    options?: { autoAssign?: boolean; assignStrategy?: string; columnMap?: string },
  ) => {
    const formData = new FormData();
    formData.append('file', file);
    if (options?.autoAssign) {
      formData.append('auto_assign', 'true');
      formData.append('assign_strategy', options.assignStrategy ?? 'rules');
    }
    if (options?.columnMap) {
      formData.append('column_map', options.columnMap);
    }
    return api.post<ApiResponse<ContactImportResult>>('/contacts/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  downloadTemplate: () =>
    api.get('/contacts/import/template', { responseType: 'blob' }),

  getActivities: (contactId: string) =>
    api.get<ApiResponse<Activity[]>>(`/contacts/${contactId}/activities`),

  createActivity: (contactId: string, data: { deal_id: string; type: string; content?: string; follow_date?: string }) =>
    api.post<ApiResponse<Activity>>(`/contacts/${contactId}/activities`, data),
};
