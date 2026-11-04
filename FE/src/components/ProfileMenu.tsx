import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { IssuerAvatar } from '@/components/IssuerAvatar';
import { useCopy } from '@/lib/useCopy';
import type { UserProfile } from '@/types/api';

/** 헤더 우측 프로필 드롭다운 (DID 복사, 바로가기, 로그아웃) */
export function ProfileMenu({ user, onLogout }: { user: UserProfile; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { copy, isCopied } = useCopy();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const itemClass =
    'flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-[14px] text-[var(--color-text-sub)] hover:bg-[var(--color-surface)]';

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-xl py-1 pr-2 pl-1 hover:bg-[var(--color-surface)]"
      >
        <IssuerAvatar issuer={user.name} size="sm" />
        <span className="hidden text-[15px] font-medium text-[var(--color-text-sub)] md:inline">{user.name} 님</span>
        <span aria-hidden="true" className="text-[11px] text-[var(--color-text-muted)]">
          ▾
        </span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-2 w-72 rounded-2xl border border-[var(--color-border)] bg-white p-2 shadow-lg"
        >
          <div className="px-3 py-2">
            <p className="truncate text-[15px] font-bold">{user.name}</p>
            <p className="truncate text-[13px] text-[var(--color-text-muted)]">{user.email}</p>
            <p className="font-mono-did mt-1 truncate text-[12px] text-[var(--color-text-muted)]">{user.did}</p>
          </div>
          <div className="my-1 h-px bg-[var(--color-border)]" />
          <button type="button" role="menuitem" className={itemClass} onClick={() => copy(user.did)}>
            내 DID 복사
            {isCopied() && <span className="text-[12px] font-semibold text-[var(--color-success)]">복사됨</span>}
          </button>
          <Link to="/dashboard" role="menuitem" className={itemClass} onClick={() => setOpen(false)}>
            자격증명 지갑
          </Link>
          <Link to="/issue" role="menuitem" className={itemClass} onClick={() => setOpen(false)}>
            새 자격증명 발급
          </Link>
          <div className="my-1 h-px bg-[var(--color-border)]" />
          <button
            type="button"
            role="menuitem"
            className={`${itemClass} text-[var(--color-danger)]`}
            onClick={() => {
              setOpen(false);
              onLogout();
            }}
          >
            로그아웃
          </button>
        </div>
      )}
    </div>
  );
}
