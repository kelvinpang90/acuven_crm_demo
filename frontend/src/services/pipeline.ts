import api from './api';
import type { ApiResponse, DealStatus } from '@/types';

export interface PipelineStageDeal {
  id: string;
  contact_id: string;
  contact_name: string;
  contact_company: string | null;
  title: string | null;
  amount: number;
  source: string | null;
  priority: string;
  status: string;
  assigned_to: string | null;
  assigned_to_name: string | null;
  updated_at: string | null;
}

export interface PipelineStage {
  status: string;
  count: number;
  total_value: number;
  deals: PipelineStageDeal[];
}

export interface PipelineData {
  stages: PipelineStage[];
}

export const pipelineApi = {
  getPipeline: () =>
    api.get<ApiResponse<PipelineData>>('/pipeline'),

  updateDealStatus: (dealId: string, status: DealStatus) =>
    api.put<ApiResponse<unknown>>(`/deals/${dealId}`, { status }),
};
