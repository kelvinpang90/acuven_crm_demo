import { useState, useEffect, useRef, type FormEvent, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/store/authStore';
import { usersApi } from '@/services/users';
import { contactsApi } from '@/services/contacts';
import DuplicateHint from './DuplicateHint';
import type { Contact, DuplicateResult } from '@/types';

const DUPLICATE_CHECK_DELAY_MS = 300;

const INDUSTRIES = [
  '科技/IT', '金融/保险', '医疗/健康', '教育/培训', '零售/电商',
  '制造/工业', '房地产', '咨询/服务', '餐饮/酒店', '物流/供应链',
];
const STATUSES = [
  { value: 'lead',        label: '潜在客户' },
  { value: 'following',   label: '跟进中' },
  { value: 'negotiating', label: '谈判中' },
  { value: 'won',         label: '已成交' },
  { value: 'lost',        label: '已流失' },
];
const PRIORITIES = ['high', 'mid', 'low'];

interface Props {
  initial?: Partial<Contact>;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
  submitting?: boolean;
}

export default function ContactForm({ initial, onSubmit, submitting }: Props) {
  const { t } = useTranslation('contacts');
  const user = useAuthStore((s) => s.user);
  const isEditing = !!initial?.id;
  const canArchive = isEditing && (user?.role === 'admin' || user?.role === 'manager');
  const canSelectUser = user?.role === 'admin' || user?.role === 'manager';

  const [form, setForm] = useState({
    name: initial?.name || '',
    company: initial?.company || '',
    industry: initial?.industry || '',
    email: initial?.email || '',
    phone: initial?.phone || '',
    address: initial?.address || '',
    notes: initial?.notes || '',
    tags: initial?.tags || [] as string[],
    is_archived: initial?.is_archived ?? 0,
    assigned_to: initial?.assigned_to || '',
    // Initial deal (only used when creating a new contact)
    initial_status: 'lead',
    initial_priority: 'mid',
    initial_amount: '0',
    initial_title: '',
  });
  const [tagInput, setTagInput] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [userOptions, setUserOptions] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    if (!canSelectUser) return;
    usersApi.getUsers().then((res) => setUserOptions(res.data.data ?? []));
  }, [canSelectUser]);

  // Possible duplicates, new contacts only (docs/contact-duplicate-hints.md 5.1, 6.4, 9.6):
  // a hint, never a block on saving.
  const [duplicates, setDuplicates] = useState<DuplicateResult | null>(null);
  const duplicateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const duplicateSeq = useRef(0);

  useEffect(() => () => {
    if (duplicateTimer.current) clearTimeout(duplicateTimer.current);
    duplicateSeq.current += 1;
  }, []);

  const scheduleDuplicateCheck = () => {
    if (isEditing) return;
    if (duplicateTimer.current) clearTimeout(duplicateTimer.current);
    duplicateTimer.current = null;
    // Any newer blur supersedes a request still in flight.
    const seq = ++duplicateSeq.current;
    const query = {
      name: form.name.trim(),
      company: form.company.trim(),
      email: form.email.trim(),
      phone: form.phone.trim(),
    };
    if (!query.name && !query.company && !query.email && !query.phone) {
      setDuplicates(null);
      return;
    }
    duplicateTimer.current = setTimeout(async () => {
      duplicateTimer.current = null;
      try {
        const res = await contactsApi.checkDuplicates({
          name: query.name || null,
          company: query.company || null,
          email: query.email || null,
          phone: query.phone || null,
        });
        if (seq === duplicateSeq.current) setDuplicates(res.data.data);
      } catch { /* ignore */ }
    }, DUPLICATE_CHECK_DELAY_MS);
  };

  const set = (key: string, value: unknown) => {
    setForm((p) => ({ ...p, [key]: value }));
    if (errors[key]) setErrors((p) => { const n = { ...p }; delete n[key]; return n; });
  };

  const validate = () => {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = t('common:required');
    if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) e.email = t('common:invalidEmail');
    if (isEditing && canSelectUser && !form.assigned_to) e.assigned_to = t('common:required');
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    const payload: Record<string, unknown> = {
      name: form.name,
      company: form.company || null,
      industry: form.industry || null,
      email: form.email || null,
      phone: form.phone || null,
      address: form.address || null,
      notes: form.notes || null,
      tags: form.tags.length ? form.tags : null,
      is_archived: form.is_archived,
      assigned_to: form.assigned_to || null,
    };
    if (!isEditing) {
      payload.initial_status = form.initial_status;
      payload.initial_priority = form.initial_priority;
      payload.initial_amount = parseFloat(form.initial_amount) || 0;
      payload.initial_title = form.initial_title || null;
    }
    await onSubmit(payload);
  };

  const addTag = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && tagInput.trim()) {
      e.preventDefault();
      if (!form.tags.includes(tagInput.trim())) {
        set('tags', [...form.tags, tagInput.trim()]);
      }
      setTagInput('');
    }
  };

  const removeTag = (tag: string) => set('tags', form.tags.filter((t) => t !== tag));

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {!isEditing && duplicates && (
        <DuplicateHint visible={duplicates.visible} hidden_count={duplicates.hidden_count} />
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label={`${t('common:name')} *`} error={errors.name}>
          <input className="input" value={form.name} onChange={(e) => set('name', e.target.value)} onBlur={scheduleDuplicateCheck} />
        </Field>
        <Field label={t('common:company')}>
          <input className="input" value={form.company} onChange={(e) => set('company', e.target.value)} onBlur={scheduleDuplicateCheck} />
        </Field>
        <Field label={t('common:industry')}>
          <select className="input" value={form.industry} onChange={(e) => set('industry', e.target.value)}>
            <option value="">--</option>
            {INDUSTRIES.map((v) => <option key={v} value={v}>{t(`industries.${v}`, v)}</option>)}
          </select>
        </Field>
        {canSelectUser && (
          <Field label={t('assignedTo')} error={errors.assigned_to}>
            <select className="input" value={form.assigned_to} onChange={(e) => set('assigned_to', e.target.value)}>
              {!isEditing && <option value="">{t('autoAssign')}</option>}
              {userOptions.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          </Field>
        )}
        <Field label={t('common:email')} error={errors.email}>
          <input type="email" className="input" value={form.email} onChange={(e) => set('email', e.target.value)} onBlur={scheduleDuplicateCheck} />
        </Field>
        <Field label={t('common:phone')}>
          <input className="input" value={form.phone} onChange={(e) => set('phone', e.target.value)} onBlur={scheduleDuplicateCheck} />
        </Field>
      </div>

      <Field label={t('common:address')}>
        <input className="input" value={form.address} onChange={(e) => set('address', e.target.value)} />
      </Field>

      <Field label={t('common:notes')}>
        <textarea className="input min-h-[80px]" value={form.notes} onChange={(e) => set('notes', e.target.value)} />
      </Field>

      <Field label={t('common:tags')}>
        <div className="flex flex-wrap gap-1 mb-1">
          {form.tags.map((tag) => (
            <span key={tag} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-primary/15 text-primary">
              {tag}
              <button type="button" onClick={() => removeTag(tag)} className="hover:text-red-400">&times;</button>
            </span>
          ))}
        </div>
        <input
          className="input"
          placeholder="press enter after input"
          value={tagInput}
          onChange={(e) => setTagInput(e.target.value)}
          onKeyDown={addTag}
        />
      </Field>

      {/* Initial deal section — only shown when creating a new contact */}
      {!isEditing && (
        <div className="border border-dark-border rounded-lg p-3 space-y-3">
          <p className="text-xs font-medium text-text-muted uppercase tracking-wide">{t('initialDeal')}</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t('common:status')}>
              <select className="input" value={form.initial_status} onChange={(e) => set('initial_status', e.target.value)}>
                {STATUSES.map((s) => <option key={s.value} value={s.value}>{t(`common:statusLabels.${s.value}`, s.label)}</option>)}
              </select>
            </Field>
            <Field label={t('common:priority')}>
              <select className="input" value={form.initial_priority} onChange={(e) => set('initial_priority', e.target.value)}>
                {PRIORITIES.map((v) => <option key={v} value={v}>{t(`common:priorityLabels.${v}`, v)}</option>)}
              </select>
            </Field>
            <Field label={t('dealValue')}>
              <input type="number" min={0} className="input" value={form.initial_amount} onChange={(e) => set('initial_amount', e.target.value)} />
            </Field>
            <Field label={t('dealTitle')}>
              <input className="input" value={form.initial_title} onChange={(e) => set('initial_title', e.target.value)} />
            </Field>
          </div>
        </div>
      )}

      {canArchive && (
        <label className="flex items-center gap-3 cursor-pointer select-none">
          <div
            onClick={() => set('is_archived', form.is_archived ? 0 : 1)}
            className={`relative w-10 h-5 rounded-full transition-colors ${
              form.is_archived ? 'bg-amber-500' : 'bg-dark-border'
            }`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
              form.is_archived ? 'translate-x-5' : ''
            }`} />
          </div>
          <span className="text-sm text-text-secondary">{t('archive')}</span>
          {!!form.is_archived && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">{t('archived')}</span>
          )}
        </label>
      )}

      <button type="submit" disabled={submitting} className="btn-primary w-full h-11">
        {submitting ? '...' : t('common:save')}
      </button>
    </form>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-text-secondary mb-1">{label}</label>
      {children}
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
}
