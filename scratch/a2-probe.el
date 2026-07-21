;; A.2 evidence probes — batch, emacs-nox 29.3, pinned ubuntu:24.04
(defun p (label value) (princ (format "%s=%s\n" label value)))

;; --- Probe 1: basename naming + <2> collision ---
(find-file "/work/aaa/probe.txt")
(p "P1-name-a" (buffer-name))
(find-file "/work/bbb/probe.txt")
(p "P1-name-b" (buffer-name))

;; --- Probe 2: per-buffer undo isolation (fresh buffers, not the RO mount) ---
(set-buffer (get-buffer-create "undo-a"))
(erase-buffer) (insert "AAA") (undo-boundary)
(set-buffer (get-buffer-create "undo-b"))
(erase-buffer) (insert "BBB") (undo-boundary)
(set-buffer "undo-a")
(undo)
(p "P2-after-undo" (buffer-string))
(set-buffer "undo-b")
(p "P2-b-intact" (buffer-string))

;; --- Probe 3: chain breaks across switch ---
(set-buffer (get-buffer-create "chain-a"))
(erase-buffer) (insert "one two")
(goto-char (point-min))
(kill-line)
(set-buffer (get-buffer-create "chain-b"))
(erase-buffer) (insert "three four")
(goto-char (point-min))
(kill-line)
(p "P3-ring-head" (current-kill 0 t))
(p "P3-ring-second" (current-kill 1 t))
(p "P3-appended" (string= (current-kill 0 t) "three fourone two"))
;; control: same-buffer consecutive kills DO append
(set-buffer (get-buffer-create "chain-c"))
(erase-buffer) (insert "xx yy")
(goto-char (point-min))
(kill-line)
(goto-char (point-max))
(insert "zz")
(goto-char (point-min))
(kill-line)
(p "P3-same-buffer-head" (current-kill 0 t))
(p "P3-same-buffer-second" (current-kill 1 t))

;; --- Probe 4: window points independent ---
(set-buffer (get-buffer-create "win-buf"))
(erase-buffer) (insert "line1\nline2\nline3\nline4")
(delete-other-windows)
(switch-to-buffer "win-buf")
(goto-char (point-min))
(split-window-below)
(goto-char (point-min)) (forward-line 2)
(let ((top (selected-window)))
  (other-window 1)
  (p "P4-bottom-initial-point" (point))
  (goto-char (point-max))
  (other-window 1)
  (p "P4-top-point" (point))
  (p "P4-same-window" (eq (selected-window) top)))

;; --- Probe 5: unknown name / other-buffer default ---
(switch-to-buffer "brand-new-name")
(p "P5-new-buffer" (buffer-name))
(p "P5-new-content" (buffer-string))
(p "P5-other-buffer" (buffer-name (other-buffer)))

;; --- Probe 6: split too small (batch frame is tiny) ---
(condition-case err
    (progn
      (delete-other-windows)
      (while (> (length (window-list)) 0)
        (split-window-below)
        (when (> (length (window-list)) 10) (error "stop")))
      (p "P6-split-count" (length (window-list))))
  (error (p "P6-error" (error-message-string err))))
