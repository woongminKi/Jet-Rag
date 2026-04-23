'use client';

import { Menu, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface HeaderMobileToggleProps {
  open: boolean;
  onToggle: () => void;
}

export function HeaderMobileToggle({ open, onToggle }: HeaderMobileToggleProps) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className="md:hidden"
      aria-label="메뉴"
      aria-expanded={open}
      aria-controls="mobile-menu-panel"
      onClick={onToggle}
    >
      {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
    </Button>
  );
}
