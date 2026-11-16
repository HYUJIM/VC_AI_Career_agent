import { AnimatePresence, motion } from 'motion/react';
import { type ReactNode, useEffect } from 'react';
import { createPortal } from 'react-dom';

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** 하단 고정 버튼 영역 */
  footer?: ReactNode;
}

/** 화면 중앙(모바일은 하단 시트) 모달. ESC·배경 클릭으로 닫힌다. */
export function Modal({ open, title, onClose, children, footer }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  // body로 포털: 페이지 전환 애니메이션(transform)이 걸린 조상 안에서도 fixed 위치가 깨지지 않게 한다.
  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          key="modal-backdrop"
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-5"
          onClick={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-t-3xl bg-[var(--color-card)] sm:rounded-3xl"
            onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, y: 24, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 420, damping: 34 }}
          >
            <div className="flex items-center justify-between gap-3 px-6 pt-5 pb-3">
              <h2 className="text-[17px] font-bold">{title}</h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="닫기"
                className="h-8 w-8 shrink-0 rounded-lg text-xl leading-none text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 pb-5">{children}</div>
            {footer && <div className="flex gap-2 border-t border-[var(--color-border)] px-6 py-4">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
