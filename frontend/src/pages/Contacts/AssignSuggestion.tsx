import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { contactsApi } from '@/services/contacts';
import type { AssignCandidate, AssignSuggestionResult } from '@/types';

interface Props {
  name: string;
  company: string;
  industry: string;
  address: string;
  selectedUserId: string;
  onSelect: (userId: string) => void;
}

// Suggested assignee (docs/contact-assign-suggest.md sections 1, 3 and 4): a rule dry run
// and per-candidate numbers; "pick" only changes the owner dropdown, saving is unchanged.
export default function AssignSuggestion({ name, company, industry, address, selectedUserId, onSelect }: Props) {
  const { t } = useTranslation('contacts');
  const [result, setResult] = useState<AssignSuggestionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const seq = useRef(0);

  // The dry run is only valid for the values it was run with: any change clears it.
  useEffect(() => {
    seq.current += 1;
    setResult(null);
    setFailed(false);
    setLoading(false);
  }, [name, company, industry, address]);

  useEffect(() => () => { seq.current += 1; }, []);

  const load = async () => {
    const current = ++seq.current;
    setLoading(true);
    setFailed(false);
    setResult(null);
    try {
      const res = await contactsApi.getAssignSuggestion({
        name: name.trim() || null,
        company: company.trim() || null,
        industry: industry.trim() || null,
        address: address.trim() || null,
      });
      if (current !== seq.current) return;
      const data = res.data.data;
      if (data) setResult(data);
      else setFailed(true);
    } catch {
      if (current === seq.current) setFailed(true);
    } finally {
      if (current === seq.current) setLoading(false);
    }
  };

  const winRate = (c: AssignCandidate) =>
    c.total_90d > 0
      ? t('assignSuggestWinRate', { won: c.won_90d, total: c.total_90d, percent: Math.round((c.won_90d / c.total_90d) * 100) })
      : t('assignSuggestNone');

  const rulePick = result?.rule_pick;
  const showIndustry = !!result && result.candidates.some((c) => c.industry_contact_count !== null);
  const pickButton = (userId: string) => (
    <button type="button" onClick={() => onSelect(userId)} className="text-xs text-primary hover:underline whitespace-nowrap">
      {t('assignSuggestPick')}
    </button>
  );

  return (
    <div className="mt-2 space-y-2">
      <button type="button" onClick={load} disabled={loading} className="text-xs text-primary hover:underline disabled:opacity-50 disabled:no-underline">
        {loading ? t('assignSuggestLoading') : t('assignSuggestButton')}
      </button>
      {(loading || failed || result) && (
        <div className="rounded-lg border border-dark-border p-3 space-y-2 text-sm">
          <p className="text-xs text-text-muted">{t('assignSuggestBasis')}</p>
          {failed && <p className="text-xs text-red-400">{t('assignSuggestFailed')}</p>}
          {rulePick && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-text-secondary">
                {rulePick.user_id === null
                  ? t('assignSuggestNoSales')
                  : rulePick.fallback
                    ? t('assignSuggestFallback', { name: rulePick.user_name })
                    : t('assignSuggestRuleHit', { name: rulePick.user_name, rule: rulePick.rule_name })}
              </span>
              {rulePick.user_id !== null && (
                rulePick.selectable
                  ? pickButton(rulePick.user_id)
                  : <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">{t('assignSuggestNotSelectable')}</span>
              )}
            </div>
          )}
          {result && result.candidates.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-text-muted">
                    <th className="py-1 pr-2 font-medium">{t('assignSuggestColName')}</th>
                    <th className="py-1 pr-2 font-medium">{t('assignSuggestColContacts')}</th>
                    <th className="py-1 pr-2 font-medium">{t('assignSuggestColOpenDeals')}</th>
                    <th className="py-1 pr-2 font-medium">{t('assignSuggestColWinRate')}</th>
                    {showIndustry && <th className="py-1 pr-2 font-medium">{t('assignSuggestColIndustry')}</th>}
                    <th className="py-1" />
                  </tr>
                </thead>
                <tbody>
                  {result.candidates.map((c) => {
                    const selected = c.user_id === selectedUserId;
                    return (
                      <tr key={c.user_id} className={`border-t border-dark-border ${selected ? 'bg-primary/10' : ''}`}>
                        <td className="py-1 pr-2 text-text-primary whitespace-nowrap">
                          {c.name}
                          {selected && (
                            <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] bg-primary/15 text-primary">{t('assignSuggestSelected')}</span>
                          )}
                        </td>
                        <td className="py-1 pr-2">{c.contact_count}</td>
                        <td className="py-1 pr-2">{c.open_deal_count}</td>
                        <td className="py-1 pr-2 whitespace-nowrap">{winRate(c)}</td>
                        {showIndustry && (
                          <td className="py-1 pr-2 whitespace-nowrap">
                            {c.industry_contact_count === null
                              ? '-'
                              : t('assignSuggestIndustryValue', { contacts: c.industry_contact_count, won: c.industry_won_count ?? 0 })}
                          </td>
                        )}
                        <td className="py-1 text-right">{pickButton(c.user_id)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
