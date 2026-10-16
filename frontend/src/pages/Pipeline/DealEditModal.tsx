import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { dealsApi } from '@/services/deals';
import { usersApi } from '@/services/users';
import { useAuthStore } from '@/store/authStore';
import type { PipelineStageDeal } from '@/services/pipeline';
import type { User, DealStatus } from '@/types';
import DealAiNextStep from './DealAiNextStep';

const STATUSES: DealStatus[] = ['lead', 'following', 'negotiating', 'won', 'lost'];
const PRIORITIES = ['high', 'mid', 'low'];

interface Props {
  deal: PipelineStageDeal;
  onClose: () => void;
  onSave: () => void;
}

export default function DealEditModal({ deal, onClose, onSave }: Props) {
  const { t } = useTranslation('common');
  const currentUser = useAuthStore((s) => s.user);
  const canAssign = currentUser?.role === 'admin' || currentUser?.role === 'manager';

  const [form, setForm] = useState({
    title: deal.title ?? '',
    amount: String(deal.amount),
    priority: deal.priority,
    status: deal.status as DealStatus,
    assigned_to: deal.assigned_to ?? '',
  });
  const [users, setUsers] = useState<User[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (canAssign) {
      usersApi.getUsers().then((res) => setUsers(res.data.data ?? [])).catch(() => {});
    }
  }, [canAssign]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await dealsApi.updateDeal(deal.id, {
        title: form.title || undefined,
        amount: parseFloat(form.amount) || 0,
        priority: form.priority,
        status: form.status,
        assigned_to: canAssign ? (form.assigned_to || undefined) : undefined,
      });
      onSave();
    } catch { /* ignore */ }
    setSaving(false);
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-50" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-dark-card border border-dark-border rounded-xl w-full max-w-md pointer-events-auto shadow-xl">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-dark-border">
            <h2 className="text-base font-semibold text-text-primary">
              {deal.contact_name}
            </h2>
            <button
              onClick={onClose}
              className="p-1 rounded hover:bg-dark-hover text-text-muted hover:text-text-primary transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          {/* Body */}
          <div className="p-4 space-y-4">
            {/* Title */}
            <div>
              <label className="block text-xs text-text-muted mb-1">{t('dealFields.title', 'Title')}</label>
              <input
                type="text"
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-primary/60"
                placeholder="Deal title"
              />
            </div>

            {/* Amount */}
            <div>
              <label className="block text-xs text-text-muted mb-1">{t('dealFields.amount', 'Amount (MYR)')}</label>
              <input
                type="number"
                value={form.amount}
                onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))}
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-primary/60"
                min="0"
                step="0.01"
              />
            </div>

            {/* Priority */}
            <div>
              <label className="block text-xs text-text-muted mb-1">{t('dealFields.priority', 'Priority')}</label>
              <div className="flex gap-2">
                {PRIORITIES.map((p) => (
                  <button
                    key={p}
                    onClick={() => setForm((f) => ({ ...f, priority: p }))}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                      form.priority === p
                        ? 'bg-primary/20 border-primary text-primary'
                        : 'bg-dark-bg border-dark-border text-text-muted hover:border-primary/40'
                    }`}
                  >
                    {t(`priority.${p}`, p)}
                  </button>
                ))}
              </div>
            </div>

            {/* Status */}
            <div>
              <label className="block text-xs text-text-muted mb-1">{t('dealFields.status', 'Status')}</label>
              <select
                value={form.status}
                onChange={(e) => setForm((f) => ({ ...f, status: e.target.value as DealStatus }))}
                className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-primary/60"
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t(`statusLabels.${s}`, s)}
                  </option>
                ))}
              </select>
            </div>

            {/* Assigned To (admin/manager only) */}
            {canAssign && (
              <div>
                <label className="block text-xs text-text-muted mb-1">{t('dealFields.assignedTo', 'Sales')}</label>
                <select
                  value={form.assigned_to}
                  onChange={(e) => setForm((f) => ({ ...f, assigned_to: e.target.value }))}
                  className="w-full bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-primary/60"
                >
                  <option value="">-- {t('unassigned', 'Unassigned')} --</option>
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>{u.name}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <DealAiNextStep dealId={deal.id} status={deal.status as DealStatus} />

          {/* Footer */}
          <div className="flex justify-end gap-2 p-4 border-t border-dark-border">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors"
            >
              {t('cancel', 'Cancel')}
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {saving ? t('saving', 'Saving...') : t('save', 'Save')}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
