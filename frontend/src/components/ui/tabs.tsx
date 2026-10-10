'use client';

import { cn } from '@/lib/utils';
import { forwardRef } from 'react';

interface TabsProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  children: React.ReactNode;
  className?: string;
}

export const Tabs = forwardRef<HTMLDivElement, TabsProps<string>>(
  ({ value, onValueChange, children, className }, ref) => {
    return (
      <div ref={ref} className={cn('space-y-4', className)}>
        {children}
      </div>
    );
  }
);
Tabs.displayName = 'Tabs';

interface TabsListProps {
  children: React.ReactNode;
  className?: string;
}

export const TabsList = forwardRef<HTMLDivElement, TabsListProps>(
  ({ children, className }, ref) => {
    return (
      <div ref={ref} role="tablist" className={cn('flex flex-wrap gap-2', className)}>
        {children}
      </div>
    );
  }
);
TabsList.displayName = 'TabsList';

interface TabsTriggerProps {
  value: string;
  children: React.ReactNode;
  className?: string;
  onValueChange?: (value: string) => void;
}

export const TabsTrigger = forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ value, children, className, onValueChange }, ref) => {
    return (
      <button
        ref={ref}
        role="tab"
        aria-selected={false}
        onClick={() => onValueChange?.(value)}
        className={cn(
          'neo neo-lift-sm inline-flex items-center gap-2 rounded-chip px-3.5 py-2 text-sm font-bold',
          'bg-surface text-ink-muted hover:text-ink'
        )}
      >
        {children}
      </button>
    );
  }
);
TabsTrigger.displayName = 'TabsTrigger';

interface TabsContentProps {
  value: string;
  children: React.ReactNode;
  className?: string;
}

export const TabsContent = forwardRef<HTMLDivElement, TabsContentProps>(
  ({ value, children, className }, ref) => {
    return (
      <div ref={ref} role="tabpanel" className={cn('', className)}>
        {children}
      </div>
    );
  }
);
TabsContent.displayName = 'TabsContent';