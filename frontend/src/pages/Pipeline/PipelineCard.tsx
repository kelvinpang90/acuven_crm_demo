import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { formatMYR } from '@/utils/currency';
import Badge from '@/components/common/Badge';
import SourceBadge from '@/components/common/SourceBadge';
import type { PipelineStageDeal } from '@/services/pipeline';

interface Props {
  deal: PipelineStageDeal;
  onEdit: (deal: PipelineStageDeal) => void;
}

export default function PipelineCard({ deal, onEdit }: Props) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: deal.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="bg-dark-bg border border-dark-border rounded-lg p-3 hover:border-primary/30 transition-colors relative group"
    >
      {/* Drag handle area */}
      <div
        {...attributes}
        {...listeners}
        className="cursor-grab active:cursor-grabbing"
      >
        <div className="flex items-center gap-2 mb-1">
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-white text-[10px] font-bold shrink-0">
            {deal.contact_name.charAt(0)}
          </div>
          <span className="text-sm font-medium text-text-primary truncate">{deal.contact_name}</span>
        </div>
        {deal.title && (
          <p className="text-xs text-primary/80 truncate mb-0.5">{deal.title}</p>
        )}
        {deal.contact_company && (
          <p className="text-xs text-text-muted truncate mb-1">{deal.contact_company}</p>
        )}
        <div className="flex items-center justify-between mt-2">
          <div className="flex items-center gap-1.5 min-w-0">
            <Badge value={deal.priority} type="priority" size="sm" />
            {deal.source && <SourceBadge value={deal.source} />}
          </div>
          <span className="text-xs font-medium text-text-secondary">
            {formatMYR(deal.amount)}
          </span>
        </div>
        {deal.assigned_to_name && (
          <p className="text-xs text-text-muted mt-1.5 truncate">👤 {deal.assigned_to_name}</p>
        )}
      </div>

      {/* Edit button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onEdit(deal);
        }}
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-dark-hover text-text-muted hover:text-text-primary"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </button>
    </div>
  );
}
