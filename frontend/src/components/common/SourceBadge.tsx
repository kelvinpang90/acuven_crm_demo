import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

// The ai_chatbot_demo industry bot a deal came in through.
const sourceStyles: Record<string, string> = {
  banking:    'text-emerald-400 bg-emerald-500/15',
  food:       'text-lime-600 bg-lime-500/15',
  hotel:      'text-sky-400 bg-sky-500/15',
  realestate: 'text-teal-400 bg-teal-500/15',
  retail:     'text-pink-400 bg-pink-500/15',
  saas:       'text-violet-400 bg-violet-500/15',
};

export default function SourceBadge({ value }: { value: string }) {
  const { t } = useTranslation('common');
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full font-medium px-2 py-0.5 text-xs whitespace-nowrap',
        sourceStyles[value] || 'text-text-secondary bg-dark-hover'
      )}
    >
      {t(`sourceLabels.${value}`, { defaultValue: value })}
    </span>
  );
}
