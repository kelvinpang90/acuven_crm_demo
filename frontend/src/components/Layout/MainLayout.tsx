import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import { useBreakpoint } from '@/hooks/useBreakpoint';
import Sidebar from './Sidebar';
import BottomTabBar from './BottomTabBar';
import TaskReminder from '@/components/common/TaskReminder';
import NewMessageAlert from '@/components/common/NewMessageAlert';
import clsx from 'clsx';

export default function MainLayout() {
  const breakpoint = useBreakpoint();
  const [collapsed, setCollapsed] = useState(breakpoint === 'md');

  const isMobile = breakpoint === 'sm';

  return (
    <div className="min-h-screen bg-dark-bg">
      {!isMobile && (
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
      )}

      <main
        className={clsx(
          'min-h-screen transition-all duration-300',
          isMobile ? 'pb-16' : collapsed ? 'ml-16' : 'ml-60'
        )}
      >
        <div className="p-4 lg:p-6">
          <Outlet />
        </div>
      </main>

      {isMobile && <BottomTabBar />}
      {/* Top-right cards stack here instead of overlapping. */}
      <div className="fixed top-4 right-4 z-50 w-80 max-w-[calc(100vw-2rem)] flex flex-col gap-3">
        <NewMessageAlert />
        <TaskReminder />
      </div>
    </div>
  );
}
