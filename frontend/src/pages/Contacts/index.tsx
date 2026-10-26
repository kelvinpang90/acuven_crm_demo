import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { contactsApi, type ContactListParams } from '@/services/contacts';
import { autocountApi } from '@/services/autocount';
import { useIsMobile } from '@/hooks/useBreakpoint';
import { useAuthStore } from '@/store/authStore';
import { formatMYR } from '@/utils/currency';
import Pagination from '@/components/common/Pagination';
import SearchInput from '@/components/common/SearchInput';
import EmptyState from '@/components/common/EmptyState';
import Skeleton from '@/components/common/Skeleton';
import Modal from '@/components/common/Modal';
import SourceBadge from '@/components/common/SourceBadge';
import ContactDetailPanel from './ContactDetailPanel';
import ContactForm from './ContactForm';
import type { Contact } from '@/types';

type SortField = 'deal_value' | 'last_contact' | 'created_at';

export default function ContactsPage() {
  const { t } = useTranslation('contacts');
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const user = useAuthStore((s) => s.user);
  const canArchive = user?.role === 'admin' || user?.role === 'manager';
  // The sync route is admin/manager only.
  const canSync = user?.role === 'admin' || user?.role === 'manager';
  // Unassigned contacts are only visible to admins, so only they get the filter.
  const isAdmin = user?.role === 'admin';

  const [contacts, setContacts] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [params, setParams] = useState<ContactListParams>({ page: 1, page_size: 20, is_archived: 0 });
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [selected, setSelected] = useState<Contact | null>(null);
  const [editing, setEditing] = useState<Contact | null>(null);
  const [deleting, setDeleting] = useState<Contact | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [unassignedCount, setUnassignedCount] = useState(0);

  const loadUnassignedCount = useCallback(async () => {
    if (!isAdmin) return;
    try {
      const res = await contactsApi.getContacts({
        unassigned: 1,
        is_archived: params.is_archived,
        page: 1,
        page_size: 1,
      });
      setUnassignedCount(res.data.data.total);
    } catch { /* ignore */ }
  }, [isAdmin, params.is_archived]);

  const load = useCallback(async () => {
    setLoading(true);
    loadUnassignedCount();
    try {
      const res = await contactsApi.getContacts(params);
      const d = res.data.data;
      setContacts(d.data);
      setTotal(d.total);
    } catch { /* ignore */ }
    setLoading(false);
  }, [params, loadUnassignedCount]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async () => {
    if (!deleting) return;
    try {
      await contactsApi.deleteContact(deleting.id);
      setDeleting(null);
      setSelected(null);
      load();
    } catch { /* ignore */ }
  };

  const handleArchive = async (is_archived: boolean) => {
    if (!selected) return;
    try {
      await contactsApi.archiveContact(selected.id, is_archived);
      setSelected(null);
      load();
    } catch { /* ignore */ }
  };

  const handleUpdate = async (data: Record<string, unknown>) => {
    if (!editing) return;
    try {
      const { is_archived, ...rest } = data;
      await contactsApi.updateContact(editing.id, rest as any);
      if (canArchive && is_archived !== undefined) {
        await contactsApi.archiveContact(editing.id, !!is_archived);
      }
      setEditing(null);
      setSelected(null);
      load();
    } catch { /* ignore */ }
  };

  const refreshSelected = async (contactId: string) => {
    load();
    try {
      const res = await contactsApi.getContact(contactId);
      setSelected((current) => (current?.id === contactId ? res.data.data : current));
    } catch { /* ignore */ }
  };

  // A possible duplicate clicked in the detail panel becomes the selected contact, so
  // edit, archive and delete act on it (docs/contact-duplicate-hints.md 9.7).
  const selectContact = async (contactId: string) => {
    try {
      const res = await contactsApi.getContact(contactId);
      setSelected((current) => (current ? res.data.data : current));
    } catch { /* ignore */ }
  };

  const handleSort = (field: SortField) => {
    const nextOrder = sortField === field && sortOrder === 'desc' ? 'asc' : 'desc';
    setSortField(field);
    setSortOrder(nextOrder);
    setParams((p) => ({ ...p, sort_by: field, order: nextOrder, page: 1 }));
  };

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const res = await autocountApi.syncNow();
      const r = res.data.data;
      setSyncMessage(t('syncResult', {
        invoices: r.invoices_synced,
        quotations: r.quotations_synced,
        unmatched: r.invoices_unmatched + r.quotations_unmatched,
      }));
      load();
    } catch {
      setSyncMessage(t('syncFailed'));
    }
    setSyncing(false);
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <span className="text-text-muted/40 ml-1">↕</span>;
    return <span className="text-primary ml-1">{sortOrder === 'asc' ? '↑' : '↓'}</span>;
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-text-primary">{t('title')}</h1>
          {canArchive && (
            <button
              onClick={() => setParams((p) => ({ ...p, is_archived: p.is_archived ? 0 : 1, page: 1 }))}
              className={`text-xs px-2 py-1 rounded border transition-colors ${
                params.is_archived
                  ? 'border-primary text-primary bg-primary/10'
                  : 'border-dark-border text-text-muted hover:text-text-secondary'
              }`}
            >
              {params.is_archived ? t('viewActive') : t('viewArchived')}
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {canSync && (
            <button onClick={handleSync} disabled={syncing} className="btn-secondary text-sm">
              {syncing ? t('syncing') : t('syncNow')}
            </button>
          )}
          <button onClick={() => navigate('/contacts/new')} className="btn-primary text-sm">
            + {t('newContact')}
          </button>
        </div>
      </div>

      {syncMessage && (
        <div className="mb-4 text-xs px-3 py-2 rounded-lg bg-dark-hover text-text-secondary">
          {syncMessage}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        <div className="flex-1 min-w-[200px]">
          <SearchInput
            value={params.search || ''}
            onChange={(v) => setParams((p) => ({ ...p, search: v, page: 1 }))}
          />
        </div>
        {isAdmin && (
          <button
            onClick={() => setParams((p) => ({ ...p, unassigned: p.unassigned ? undefined : 1, page: 1 }))}
            className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg border transition-colors ${
              params.unassigned
                ? 'border-primary text-primary bg-primary/10'
                : 'border-dark-border text-text-muted hover:text-text-secondary'
            }`}
          >
            {t('unassignedOnly')}
            <span
              title={t('unassignedCount', { number: unassignedCount })}
              className={`min-w-[1.25rem] px-1.5 py-0.5 rounded-full text-center font-medium ${
                unassignedCount > 0 ? 'bg-red-500 text-white' : 'bg-dark-hover text-text-muted'
              }`}
            >
              {unassignedCount}
            </span>
          </button>
        )}
      </div>

      {/* List */}
      {loading ? (
        <Skeleton rows={6} />
      ) : contacts.length === 0 ? (
        <EmptyState message={t('noContacts')} />
      ) : isMobile ? (
        /* Mobile: Card layout */
        <div className="space-y-2">
          {contacts.map((c) => (
            <div
              key={c.id}
              onClick={() => setSelected(c)}
              className="card p-3 cursor-pointer hover:bg-dark-hover transition-colors"
            >
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-white text-sm font-bold shrink-0">
                  {c.name.charAt(0)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-text-primary truncate">{c.name}</span>
                    {c.phone_conflict && (
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-400 shrink-0">
                        {t('phoneConflict')}
                      </span>
                    )}
                    <span className="text-xs text-text-muted">{c.deal_count} {t('deals')}</span>
                    {c.sources.map((s) => <SourceBadge key={s} value={s} />)}
                  </div>
                  <p className="text-xs text-text-muted truncate">{c.company || '-'} · {formatMYR(c.total_deal_amount)}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Desktop: Table */
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-dark-border text-text-muted text-left">
                <th className="pb-2 font-medium">{t('common:name')}</th>
                <th className="pb-2 font-medium">{t('common:company')}</th>
                <th className="pb-2 font-medium">{t('common:source')}</th>
                <th className="pb-2 font-medium">{t('assignedTo')}</th>
                <th
                  className="pb-2 font-medium text-right cursor-pointer select-none hover:text-text-primary"
                  onClick={() => handleSort('deal_value')}
                >
                  {t('totalDealAmount')}<SortIcon field="deal_value" />
                </th>
                <th className="pb-2 font-medium text-right">{t('dealCount')}</th>
                <th
                  className="pb-2 font-medium pl-6 cursor-pointer select-none hover:text-text-primary"
                  onClick={() => handleSort('last_contact')}
                >
                  {t('lastContact')}<SortIcon field="last_contact" />
                </th>
              </tr>
            </thead>
            <tbody>
              {contacts.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setSelected(c)}
                  className="border-b border-dark-border/50 hover:bg-dark-hover cursor-pointer transition-colors"
                >
                  <td className="py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-white text-xs font-bold shrink-0">
                        {c.name.charAt(0)}
                      </div>
                      <span className="font-medium text-text-primary">{c.name}</span>
                      {c.phone_conflict && (
                        <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-400">
                          {t('phoneConflict')}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 text-text-secondary">{c.company || '-'}</td>
                  <td className="py-3">
                    {c.sources.length ? (
                      <div className="flex flex-wrap gap-1">
                        {c.sources.map((s) => <SourceBadge key={s} value={s} />)}
                      </div>
                    ) : (
                      <span className="text-text-muted">-</span>
                    )}
                  </td>
                  <td className="py-3 text-text-secondary">{c.assigned_to_name || '-'}</td>
                  <td className="py-3 text-right text-text-primary">{formatMYR(c.total_deal_amount)}</td>
                  <td className="py-3 text-right text-text-muted">{c.deal_count}</td>
                  <td className="py-3 pl-6 text-text-muted">{c.last_contact || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={params.page || 1}
        pageSize={params.page_size || 20}
        total={total}
        onChange={(p) => setParams((prev) => ({ ...prev, page: p }))}
      />

      {/* Detail Panel */}
      {selected && (
        <ContactDetailPanel
          contact={selected}
          onClose={() => setSelected(null)}
          onEdit={() => { setEditing(selected); setSelected(null); }}
          onDelete={() => { setDeleting(selected); setSelected(null); }}
          onArchive={handleArchive}
          onRefresh={load}
          onContactUpdated={() => refreshSelected(selected.id)}
          onSelectContact={selectContact}
        />
      )}

      {/* Edit Modal */}
      <Modal open={!!editing} onClose={() => setEditing(null)} title={t('editContact')} size="lg">
        {editing && (
          <ContactForm
            initial={editing}
            onSubmit={handleUpdate}
          />
        )}
      </Modal>

      {/* Delete Confirm */}
      <Modal open={!!deleting} onClose={() => setDeleting(null)} title={t('deleteContact')} size="sm">
        <p className="text-text-secondary mb-4">{t('common:deleteConfirm')}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={() => setDeleting(null)} className="btn-secondary text-sm">{t('common:cancel')}</button>
          <button onClick={handleDelete} className="btn-primary text-sm bg-red-500 hover:bg-red-600">{t('common:delete')}</button>
        </div>
      </Modal>
    </div>
  );
}
