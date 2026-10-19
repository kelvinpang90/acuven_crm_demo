import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { dashboardApi } from '@/services/dashboard';
import type { KpiItem, FunnelStage, LeaderboardEntry } from '@/services/dashboard';
import KpiCard from '@/components/charts/KpiCard';
import FunnelChart from '@/components/charts/FunnelChart';
import LeaderboardTable from '@/components/charts/LeaderboardTable';
import Skeleton from '@/components/common/Skeleton';
import DashboardAiInsight from './DashboardAiInsight';

const KPI_LABELS: Record<string, string> = {
  team_monthly_gmv: 'teamMonthlyGmv',
  team_target_rate: 'teamTargetRate',
  team_pipeline_value: 'teamPipelineValue',
  team_win_rate: 'teamWinRate',
  avg_sales_cycle_days: 'avgSalesCycle',
};

const KPI_CONFIG: Record<string, { prefix?: string; suffix?: string }> = {
  team_monthly_gmv: { prefix: 'RM ' },
  team_target_rate: { suffix: '%' },
  team_pipeline_value: { prefix: 'RM ' },
  team_win_rate: { suffix: '%' },
  avg_sales_cycle_days: { suffix: ' days' },
};

function currentMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export default function ManagerDashboard() {
  const { t } = useTranslation('dashboard');
  const [loading, setLoading] = useState(true);
  const [kpis, setKpis] = useState<KpiItem[]>([]);
  const [funnel, setFunnel] = useState<FunnelStage[]>([]);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [lbMonth, setLbMonth] = useState(currentMonth());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [dashRes, lbRes] = await Promise.all([
        dashboardApi.getManager(),
        dashboardApi.getTeamLeaderboard(lbMonth),
      ]);
      setKpis(dashRes.data.data.kpis);
      setFunnel(dashRes.data.data.funnel);
      setLeaderboard(lbRes.data.data.entries);
    } catch { /* ignore */ }
    setLoading(false);
  }, [lbMonth]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Skeleton rows={8} />;

  return (
    <div className="space-y-4">
      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {kpis.map((kpi) => (
          <KpiCard
            key={kpi.key}
            title={t(KPI_LABELS[kpi.key] || kpi.key)}
            value={kpi.value}
            change={kpi.change}
            {...KPI_CONFIG[kpi.key]}
          />
        ))}
      </div>

      <DashboardAiInsight />

      {/* Funnel + Leaderboard */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <FunnelChart stages={funnel} title={t('salesFunnel')} />
        <LeaderboardTable
          entries={leaderboard}
          title={t('teamLeaderboard')}
          month={lbMonth}
          onMonthChange={setLbMonth}
        />
      </div>
    </div>
  );
}
