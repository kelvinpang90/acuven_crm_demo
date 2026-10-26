import { useTranslation } from 'react-i18next';
import type { DuplicateContact } from '@/types';

interface Props {
  visible: DuplicateContact[];
  hidden_count: number;
  // Only the detail panel passes this; on the new-contact page the matches are plain text.
  onSelect?: (id: string) => void;
}

// Possible duplicates (docs/contact-duplicate-hints.md section 4): matches the user may
// open are listed, the others are only counted and never described.
export default function DuplicateHint({ visible, hidden_count, onSelect }: Props) {
  const { t } = useTranslation('contacts');

  if (visible.length === 0 && hidden_count <= 0) return null;

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 space-y-2 text-sm">
      <p className="text-xs font-medium text-amber-400">{t('duplicateTitle')}</p>
      {visible.length > 0 && (
        <ul className="space-y-1">
          {visible.map((d) => {
            const body = (
              <>
                <span className="font-medium text-text-primary">{d.name}</span>
                <span className="text-text-muted"> · {d.company || '-'} · {t('duplicateOwner', { name: d.assigned_to_name || '-' })}</span>
                <span className="flex flex-wrap gap-1 mt-0.5">
                  {d.rules.map((rule) => (
                    <span key={rule} className="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-400">
                      {t(`duplicateRules.${rule}`, rule)}
                    </span>
                  ))}
                </span>
              </>
            );
            return (
              <li key={d.id}>
                {onSelect ? (
                  <button
                    type="button"
                    onClick={() => onSelect(d.id)}
                    className="w-full text-left rounded px-1 -mx-1 hover:bg-dark-hover transition-colors"
                  >
                    {body}
                  </button>
                ) : (
                  <div>{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {hidden_count > 0 && (
        <p className="text-xs text-text-secondary">{t('duplicateHidden', { number: hidden_count })}</p>
      )}
    </div>
  );
}
