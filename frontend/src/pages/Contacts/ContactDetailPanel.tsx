import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/hooks/useBreakpoint';
import Badge from '@/components/common/Badge';
import Modal from '@/components/common/Modal';
import ActivityTimeline from './ActivityTimeline';
import ContactAiSummary from './ContactAiSummary';
import { useAuthStore } from '@/store/authStore';
import { dealsApi } from '@/services/deals';
import { autocountApi } from '@/services/autocount';
import { formatMYR } from '@/utils/currency';
import type { Contact, Deal, AutocountDocument } from '@/types';
import clsx from 'clsx';

const STATUSES = ['lead', 'following', 'negotiating', 'won', 'lost'];
const PRIORITIES = ['high', 'mid', 'low'];

interface Props {
  contact: Contact;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onArchive: (is_archived: boolean) => void;
  onRefresh?: () => void;
}

export default function ContactDetailPanel({ contact, onClose, onEdit, onDelete, onArchive, onRefresh }: Props) {
  const { t } = useTranslation('contacts');
  const isMobile = useIsMobile();
  const user = useAuthStore((s) => s.user);
  const canArchive = user?.role === 'admin' || user?.role === 'manager';

  const [deals, setDeals] = useState<Deal[]>([]);
  const [dealsLoading, setDealsLoading] = useState(true);
  const [autocountDocs, setAutocountDocs] = useState<AutocountDocument[]>([]);
  const [autocountLoading, setAutocountLoading] = useState(true);
  const [selectedDoc, setSelectedDoc] = useState<AutocountDocument | null>(null);
  const [showNewDeal, setShowNewDeal] = useState(false);
  const [newDeal, setNewDeal] = useState({ title: '', status: 'lead', priority: 'mid', amount: '0' });
  const [savingDeal, setSavingDeal] = useState(false);

  const loadDeals = async () => {
    setDealsLoading(true);
    try {
      const res = await dealsApi.getDeals(contact.id);
      setDeals(res.data.data ?? []);
    } catch { /* ignore */ }
    setDealsLoading(false);
  };

  const loadAutocountDocs = async () => {
    setAutocountLoading(true);
    try {
      const res = await autocountApi.getContactDocuments(contact.id);
      setAutocountDocs(res.data.data ?? []);
    } catch { /* ignore */ }
    setAutocountLoading(false);
  };

  useEffect(() => { loadDeals(); loadAutocountDocs(); }, [contact.id]);

  const handleAddDeal = async () => {
    setSavingDeal(true);
    try {
      await dealsApi.createDeal({
        contact_id: contact.id,
        title: newDeal.title || undefined,
        status: newDeal.status as any,
        priority: newDeal.priority,
        amount: parseFloat(newDeal.amount) || 0,
      });
      setShowNewDeal(false);
      setNewDeal({ title: '', status: 'lead', priority: 'mid', amount: '0' });
      await loadDeals();
      onRefresh?.();
    } catch { /* ignore */ }
    setSavingDeal(false);
  };

  const handleDeleteDeal = async (dealId: string) => {
    try {
      await dealsApi.deleteDeal(dealId);
      await loadDeals();
      onRefresh?.();
    } catch { /* ignore */ }
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className={clsx(
        'fixed bg-dark-card z-50 overflow-y-auto transition-transform',
        isMobile
          ? 'inset-x-0 bottom-0 top-0 w-full border-t border-dark-border animate-in slide-in-from-bottom'
          : 'top-0 right-0 h-full w-[420px] border-l border-dark-border'
      )}>
        {/* Header */}
        <div className="sticky top-0 bg-dark-card border-b border-dark-border p-4 flex items-center justify-between">
          <h3 className="font-semibold text-text-primary">{t('contactDetail')}</h3>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-4 space-y-5">
          {/* Avatar + Name */}
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-white text-lg font-bold shrink-0">
              {contact.name.charAt(0)}
            </div>
            <div>
              <h4 className="font-semibold text-text-primary">{contact.name}</h4>
              {contact.phone_conflict && (
                <span className="inline-block mt-1 px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-400">
                  {t('phoneConflict')}
                </span>
              )}
              <p className="text-sm text-text-muted">{contact.company || '-'}</p>
            </div>
          </div>

          {/* Info Grid */}
          <div className="space-y-3 text-sm">
            <InfoRow label={t('common:industry')} value={contact.industry} />
            <InfoRow label={t('assignedTo')} value={contact.assigned_to_name} />
            <InfoRow label={t('common:email')} value={contact.email} />
            <InfoRow label={t('common:phone')} value={contact.phone} />
            <InfoRow label={t('common:address')} value={contact.address} />
            <InfoRow label={t('lastContact')} value={contact.last_contact} />
            <InfoRow label={t('autocountCustomer')} value={contact.autocount_customer_code} />
          </div>

          {/* Tags */}
          {contact.tags && contact.tags.length > 0 && (
            <div>
              <p className="text-xs text-text-muted mb-1">{t('common:tags')}</p>
              <div className="flex flex-wrap gap-1">
                {contact.tags.map((tag) => (
                  <span key={tag} className="px-2 py-0.5 rounded-full text-xs bg-dark-hover text-text-secondary">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Notes */}
          {contact.notes && (
            <div>
              <p className="text-xs text-text-muted mb-1">{t('common:notes')}</p>
              <p className="text-sm text-text-secondary">{contact.notes}</p>
            </div>
          )}

          <ContactAiSummary contactId={contact.id} />

          {/* Actions */}
          <div className="flex gap-2 flex-wrap">
            <button onClick={onEdit} className="btn-primary text-sm flex-1">
              {t('common:edit')}
            </button>
            {canArchive && (
              <button
                onClick={() => onArchive(!contact.is_archived)}
                className="btn-secondary text-sm"
              >
                {contact.is_archived ? t('unarchive') : t('archive')}
              </button>
            )}
            <button onClick={onDelete} className="btn-secondary text-sm text-red-400 hover:text-red-300">
              {t('common:delete')}
            </button>
          </div>

          {/* Deals Section */}
          <div className="border-t border-dark-border pt-4">
            <div className="flex items-center justify-between mb-3">
              <p className="text-sm font-medium text-text-primary">{t('deals')} ({deals.length})</p>
              <button
                onClick={() => setShowNewDeal((v) => !v)}
                className="text-xs text-primary hover:text-primary/80"
              >
                + {t('newDeal')}
              </button>
            </div>

            {showNewDeal && (
              <div className="border border-dark-border rounded-lg p-3 mb-3 space-y-2 bg-dark-bg">
                <input
                  className="input text-sm"
                  placeholder={t('dealTitlePlaceholder')}
                  value={newDeal.title}
                  onChange={(e) => setNewDeal((p) => ({ ...p, title: e.target.value }))}
                />
                <div className="grid grid-cols-3 gap-2">
                  <select className="input text-sm" value={newDeal.status} onChange={(e) => setNewDeal((p) => ({ ...p, status: e.target.value }))}>
                    {STATUSES.map((s) => <option key={s} value={s}>{t(`common:statusLabels.${s}`, s)}</option>)}
                  </select>
                  <select className="input text-sm" value={newDeal.priority} onChange={(e) => setNewDeal((p) => ({ ...p, priority: e.target.value }))}>
                    {PRIORITIES.map((p) => <option key={p} value={p}>{t(`common:priorityLabels.${p}`, p)}</option>)}
                  </select>
                  <input
                    type="number" min={0}
                    className="input text-sm"
                    placeholder={t('dealValue')}
                    value={newDeal.amount}
                    onChange={(e) => setNewDeal((p) => ({ ...p, amount: e.target.value }))}
                  />
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowNewDeal(false)} className="btn-secondary text-xs py-1 px-3">{t('common:cancel')}</button>
                  <button onClick={handleAddDeal} disabled={savingDeal} className="btn-primary text-xs py-1 px-3">
                    {savingDeal ? '...' : t('common:save')}
                  </button>
                </div>
              </div>
            )}

            {dealsLoading ? (
              <p className="text-xs text-text-muted py-2">{t('common:loading', 'Loading...')}</p>
            ) : deals.length === 0 ? (
              <p className="text-xs text-text-muted py-2">--</p>
            ) : (
              <div className="space-y-2">
                {deals.map((d) => (
                  <div key={d.id} className="flex items-center justify-between gap-2 p-2 rounded-lg bg-dark-bg border border-dark-border/50">
                    <div className="flex-1 min-w-0">
                      {d.title && <p className="text-xs font-medium text-text-primary truncate">{d.title}</p>}
                      <div className="flex items-center gap-1 mt-0.5">
                        <Badge value={d.status} type="status" size="sm" />
                        <Badge value={d.priority} type="priority" size="sm" />
                        <span className="text-xs text-text-secondary ml-1">{formatMYR(d.amount)}</span>
                      </div>
                    </div>
                    <button
                      onClick={() => handleDeleteDeal(d.id)}
                      className="text-text-muted/40 hover:text-red-400 transition-colors shrink-0"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* AutoCount Documents Section */}
          <div className="border-t border-dark-border pt-4">
            <p className="text-sm font-medium text-text-primary mb-3">
              {t('autocountDocuments')} ({autocountDocs.length})
            </p>
            {autocountLoading ? (
              <p className="text-xs text-text-muted py-2">{t('common:loading', 'Loading...')}</p>
            ) : autocountDocs.length === 0 ? (
              <p className="text-xs text-text-muted py-2">--</p>
            ) : (
              <div className="space-y-2">
                {autocountDocs.map((doc) => (
                  <button
                    key={doc.id}
                    type="button"
                    onClick={() => setSelectedDoc(doc)}
                    className="w-full flex items-center justify-between gap-2 p-2 rounded-lg bg-dark-bg border border-dark-border/50 text-left hover:border-primary/50 transition-colors"
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-text-primary truncate">
                        {t(`autocountDocType.${doc.doc_type}`)} · {doc.doc_no}
                      </p>
                      <div className="flex items-center gap-1 mt-0.5 flex-wrap">
                        <span className="px-2 py-0.5 rounded-full text-xs bg-dark-hover text-text-secondary">
                          {doc.status || '-'}
                        </span>
                        <span className="text-xs text-text-secondary ml-1">
                          {doc.currency_code} {doc.total_amount.toFixed(2)}
                        </span>
                        {doc.doc_type === 'invoice' && doc.outstanding_amount != null && doc.outstanding_amount > 0 && (
                          <span className="text-xs text-amber-400">
                            {t('outstanding')}: {doc.currency_code} {doc.outstanding_amount.toFixed(2)}
                          </span>
                        )}
                      </div>
                    </div>
                    <svg className="w-3.5 h-3.5 text-text-muted shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                ))}
              </div>
            )}
          </div>

          <AutocountDocumentModal doc={selectedDoc} onClose={() => setSelectedDoc(null)} />

          {/* Activity Timeline */}
          <div className="border-t border-dark-border pt-4">
            <ActivityTimeline contactId={contact.id} deals={deals} />
          </div>
        </div>
      </div>
    </>
  );
}

function InfoRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex justify-between">
      <span className="text-text-muted">{label}</span>
      <span className="text-text-secondary">{value || '-'}</span>
    </div>
  );
}

function AutocountDocumentModal({ doc, onClose }: { doc: AutocountDocument | null; onClose: () => void }) {
  const { t } = useTranslation('contacts');

  return (
    <Modal
      open={!!doc}
      onClose={onClose}
      title={doc ? `${t(`autocountDocType.${doc.doc_type}`)} · ${doc.doc_no}` : undefined}
    >
      {doc && (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="px-2 py-0.5 rounded-full text-xs bg-dark-hover text-text-secondary">
              {doc.status || '-'}
            </span>
            <span className="text-text-secondary">{doc.currency_code} {doc.total_amount.toFixed(2)}</span>
          </div>

          {doc.doc_type === 'invoice' && (
            <>
              {doc.outstanding_amount != null && doc.outstanding_amount > 0 && (
                <InfoRow label={t('outstanding')} value={`${doc.currency_code} ${doc.outstanding_amount.toFixed(2)}`} />
              )}
              <InfoRow label={t('dueDate')} value={doc.due_date} />
            </>
          )}
          {doc.doc_type === 'quotation' && (
            <InfoRow label={t('validity')} value={doc.validity} />
          )}

          {doc.line_items.length === 0 ? (
            <p className="text-xs text-text-muted">--</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted">
                  <th className="text-left font-medium pb-1">{t('lineItem.description')}</th>
                  <th className="text-right font-medium pb-1">{t('lineItem.qty')}</th>
                  <th className="text-right font-medium pb-1">{t('lineItem.unitPrice')}</th>
                  <th className="text-right font-medium pb-1">{t('lineItem.subTotal')}</th>
                </tr>
              </thead>
              <tbody>
                {doc.line_items.map((item, idx) => (
                  <tr key={idx} className="text-text-secondary">
                    <td className="py-1">{item.description || item.product_code || '-'}</td>
                    <td className="py-1 text-right">{item.qty ?? '-'} {item.unit || ''}</td>
                    <td className="py-1 text-right">{item.unit_price?.toFixed(2) ?? '-'}</td>
                    <td className="py-1 text-right">{item.sub_total?.toFixed(2) ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Modal>
  );
}
