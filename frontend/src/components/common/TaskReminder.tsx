import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { tasksApi } from '@/services/tasks';
import type { Task } from '@/types';
import dayjs from 'dayjs';

export default function TaskReminder() {
  const { t } = useTranslation('tasks');
  const tc = useTranslation().t;
  const [tasks, setTasks] = useState<Task[]>([]);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const today = dayjs().format('YYYY-MM-DD');
    tasksApi.getTasks({ status: 'pending', due_before: today, page_size: 50 })
      .then((res) => {
        const todayTasks = res.data.data.data.filter(
          (task) => task.due_date && dayjs(task.due_date).format('YYYY-MM-DD') === today && !task.is_done
        );
        if (todayTasks.length > 0) {
          setTasks(todayTasks);
          setVisible(true);
        }
      })
      .catch(() => {});
  }, []);

  if (!visible || tasks.length === 0) return null;

  return (
    <div className="bg-dark-card border border-primary/30 rounded-xl shadow-lg shadow-primary/10 p-4 animate-in fade-in slide-in-from-top-2">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-text-primary">
          📋 {t('todayTasks')} ({tasks.length})
        </h4>
        <button
          onClick={() => setVisible(false)}
          className="text-text-muted hover:text-text-primary text-sm"
        >
          ✕
        </button>
      </div>
      <div className="space-y-2 max-h-60 overflow-y-auto">
        {tasks.map((task) => (
          <div
            key={task.id}
            className="flex items-center gap-2 p-2 rounded-lg bg-dark-hover text-sm"
          >
            <span className={`w-2 h-2 rounded-full shrink-0 ${
              task.priority === 'high' ? 'bg-red-400' : task.priority === 'mid' ? 'bg-yellow-400' : 'bg-blue-400'
            }`} />
            <span className="text-text-primary truncate flex-1">{task.title}</span>
            {task.contact_name && (
              <span className="text-text-muted text-xs truncate max-w-[80px]">{task.contact_name}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
