import { useEffect, useRef, type RefObject } from "react";
import { restoreFocus, type FocusBookmark } from "./useValidatedImport";

/** A DOM dialog keeps rendering/control responsive; no blocking window.confirm. */
export function ReplacementDialog({ id, pending, fallback, onCancel, onAccept }: {
  id: string;
  pending: { name: string; focus: FocusBookmark } | null;
  fallback: RefObject<HTMLInputElement | null>;
  onCancel: () => void;
  onAccept: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const accept = useRef<HTMLButtonElement>(null);
  const focus = useRef<FocusBookmark | null>(null);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (pending) {
      focus.current = pending.focus;
      if (!node.open) node.showModal();
      cancel.current?.focus();
    } else if (node.open) {
      node.close();
      restoreFocus(focus.current, fallback.current);
    }
  }, [pending, fallback]);
  useEffect(() => { const node = dialog.current; return () => { if (node?.open) node.close(); }; }, []);
  return <dialog ref={dialog} className="replacement-dialog" role="alertdialog" aria-modal="true"
    aria-labelledby={`${id}-heading`} aria-describedby={`${id}-description`}
    onCancel={event => { event.preventDefault(); onCancel(); }}
    onKeyDown={event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "Escape") { event.preventDefault(); onCancel(); }
      else if (event.key === "Enter") {
        event.preventDefault();
        if (event.target === accept.current) onAccept(); else onCancel();
      } else if (event.key === "Tab") {
        event.preventDefault();
        const buttons = [cancel.current, accept.current];
        const index = buttons.findIndex(button => button === document.activeElement);
        buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus();
      }
    }}>
    <h2 id={`${id}-heading`}>Replace the current draft?</h2>
    <p id={`${id}-description`}>The new file has been validated. Replacing it clears the current unsaved draft. Download decisions first if you want to keep them.</p>
    {pending && <p className="identity">Incoming file: {pending.name}</p>}
    <div className="replacement-actions">
      <button type="button" ref={cancel} onClick={onCancel}>Keep current draft</button>
      <button type="button" ref={accept} onClick={onAccept}>Replace current draft</button>
    </div>
  </dialog>;
}
