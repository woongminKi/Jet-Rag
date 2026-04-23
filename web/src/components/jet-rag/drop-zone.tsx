'use client';

import { useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { cn } from '@/lib/utils';
import { ACCEPT_ATTRIBUTE } from '@/lib/stages';

interface DropZoneProps {
  onFiles: (files: File[]) => void;
}

export function DropZone({ onFiles }: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);

  const handleFiles = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    onFiles(Array.from(list));
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setActive(true);
      }}
      onDragLeave={() => setActive(false)}
      onDrop={(e) => {
        e.preventDefault();
        setActive(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      className={cn(
        'relative flex h-64 cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-card text-center transition-colors',
        active
          ? 'border-primary bg-primary/5'
          : 'border-border hover:border-primary/50 hover:bg-muted/30',
      )}
    >
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Upload className="h-6 w-6" />
      </div>
      <div className="space-y-1">
        <p className="text-base font-medium text-foreground">
          파일을 끌어다 놓거나 클릭해서 선택하세요
        </p>
        <p className="text-xs text-muted-foreground">
          PDF · HWP/HWPX · DOCX · PPTX · 이미지(JPG/PNG/HEIC) · TXT/MD · 최대 50MB
        </p>
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        onChange={(e) => handleFiles(e.target.files)}
        className="absolute inset-0 cursor-pointer opacity-0"
      />
    </div>
  );
}
