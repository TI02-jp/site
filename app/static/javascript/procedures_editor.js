(function () {
    'use strict';

    function insertNodeAtCaret(editor, node) {
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0) {
            editor.appendChild(node);
            editor.appendChild(document.createElement('p'));
            return;
        }

        const range = selection.getRangeAt(0);
        range.deleteContents();
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
    }

    function createSpacerParagraph() {
        const paragraph = document.createElement('p');
        paragraph.innerHTML = '<br />';
        return paragraph;
    }

    function handleImagePaste(editor, files, hiddenInput) {
        files.forEach((file) => {
            if (!file || !file.type.startsWith('image/')) {
                return;
            }
            const reader = new FileReader();
            reader.onload = (event) => {
                const image = document.createElement('img');
                image.src = event.target?.result || '';
                image.alt = file.name || 'Imagem colada';
                image.classList.add('procedure-editor__image');
                insertNodeAtCaret(editor, image);
                image.insertAdjacentElement('afterend', createSpacerParagraph());
                syncHiddenInput(editor, hiddenInput);
            };
            reader.readAsDataURL(file);
        });
    }

    function normaliseEditorContent(editor) {
        if (!editor.innerHTML || editor.innerHTML === '<br>' || editor.innerHTML === '<div><br></div>') {
            editor.innerHTML = '';
        }
    }

    function findHiddenInput(editor) {
        const form = editor.closest('form');
        if (!form) {
            return null;
        }

        const key = editor.getAttribute('data-procedure-editor');
        if (key) {
            try {
                const selector = `[data-procedure-input="${CSS.escape(key)}"]`;
                const targeted = form.querySelector(selector);
                if (targeted) {
                    return targeted;
                }
            } catch (error) {
                // CSS.escape may not be available in very old browsers; fallback below.
            }

            const fallbackTargeted = form.querySelector(
                `[data-procedure-input="${key}"]`
            );
            if (fallbackTargeted) {
                return fallbackTargeted;
            }
        }

        return form.querySelector('[data-procedure-input]');
    }

    function syncHiddenInput(editor, hiddenInput) {
        if (!hiddenInput) {
            return;
        }
        hiddenInput.value = editor.innerHTML.trim();
    }

    document.addEventListener('DOMContentLoaded', () => {
        const editors = document.querySelectorAll('[data-procedure-editor]');
        editors.forEach((editor) => {
            const hiddenInput = findHiddenInput(editor);
            if (!hiddenInput) {
                return;
            }
            normaliseEditorContent(editor);
            syncHiddenInput(editor, hiddenInput);

            editor.addEventListener('input', () => {
                normaliseEditorContent(editor);
                syncHiddenInput(editor, hiddenInput);
            });

            editor.addEventListener('blur', () => {
                syncHiddenInput(editor, hiddenInput);
            });

            editor.addEventListener('paste', (event) => {
                const clipboard = event.clipboardData;
                if (!clipboard) {
                    return;
                }

                const imageItems = Array.from(clipboard.items || []).filter((item) => item.type && item.type.startsWith('image/'));
                if (imageItems.length === 0) {
                    return;
                }

                event.preventDefault();

                const textData = clipboard.getData('text/plain');
                if (textData) {
                    try {
                        document.execCommand('insertText', false, textData);
                    } catch (error) {
                        insertNodeAtCaret(editor, document.createTextNode(textData));
                    }
                }

                const files = imageItems
                    .map((item) => (typeof item.getAsFile === 'function' ? item.getAsFile() : null))
                    .filter(Boolean);

                if (files.length) {
                    handleImagePaste(editor, files, hiddenInput);
                }

                syncHiddenInput(editor, hiddenInput);
            });

            const form = editor.closest('form');
            if (form) {
                form.addEventListener('submit', () => {
                    normaliseEditorContent(editor);
                    syncHiddenInput(editor, hiddenInput);
                });
            }
        });
    });
})();
