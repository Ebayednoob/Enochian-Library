import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from PIL import Image, ImageTk, ImageDraw
import pymupdf # Fitz
import pdfplumber
import pytesseract 
import imagehash 
import re
import json
import os
import threading
import time
import asyncio # For LLM calls
import base64 # For image encoding for LLM
import io

# Attempt to import LLM libraries, but don't make them hard requirements initially
try:
    import google.generativeai as genai
except ImportError:
    genai = None
try:
    import openai
except ImportError:
    openai = None


# --- Configuration (can be adjusted) ---
DEFAULT_PDF_NAME = "Dictionary-of-Occult-Hermetic-Alchemical-Sigils-Symbols-Fred-Gettings-1981.pdf"
OUTPUT_JSON_PATH = "sigil_dictionary_extracted_with_sigils_metadata.json" 
OUTPUT_IMAGE_DIR = "extracted_sigil_images" 

DEFAULT_START_PAGE_EXTRACTION = 38 
DEFAULT_END_PAGE_EXTRACTION = 291 

# --- Drawing Canvas Constants ---
DRAW_CANVAS_WIDTH = 250
DRAW_CANVAS_HEIGHT = 250
DRAW_BG_COLOR = "white"
DRAW_COLOR = "black"
DRAW_LINE_WIDTH = 3

# --- Regular Expressions ---
HEADING_CLASS_RE = re.compile(r"^([A-Z0-9][A-Z0-9\s\-’,]+?)\s+([A-Z][a-z]{1,3}\.)")
BIBLIO_RE = re.compile(r"([A-Z][A-Za-z\s]+?\s\d{4}|[A-Z][A-Za-z\s]+?\s\d{1,2}C)")

POTENTIAL_SYMBOL_RE_OCR = re.compile(r"^[^\s\w.,;:'\"()\[\]?!]{1,5}$") 
COMMON_PUNCTUATION = ['.', ',', ';', ':', '(', ')', "'", '"', '[', ']', '!', '?','-', '/']


# --- Overlay Colors ---
OVERLAY_COLORS = {
    "heading": "blue",
    "text_block": "green", 
    "biblio_ref": "purple",
    "potential_symbol": "red", 
    "saved_sigil": "orange",    
    "default": "gray"
}

# --- Tesseract Configuration ---
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # Example

class PDFScannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Sigil Analyzer") 
        self.root.geometry("1400x1000") # Increased size

        self.pdf_folder_path = tk.StringVar()
        self.selected_pdf_path = tk.StringVar()
        self.all_extracted_data = [] 
        self.scanned_data_for_query = [] 
        self.current_pdf_document_fitz = None
        self.current_pdf_document_plumber = None
        self.zoom_x = 2.0 
        self.zoom_y = 2.0
        self.use_ocr_var = tk.BooleanVar(value=False) 
        self.sigil_counter = 0 

        self.draw_last_x, self.draw_last_y = None, None
        self.drawn_image_pil = Image.new("RGB", (DRAW_CANVAS_WIDTH, DRAW_CANVAS_HEIGHT), DRAW_BG_COLOR)
        self.pil_draw_context = ImageDraw.Draw(self.drawn_image_pil)

        # LLM related attributes
        self.gemini_api_key = tk.StringVar(value=os.getenv("GOOGLE_API_KEY", ""))
        self.openai_api_key = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.llm_providers = []
        if genai: self.llm_providers.append("Gemini")
        if openai: self.llm_providers.append("OpenAI")
        if not self.llm_providers: self.llm_providers.append("No LLM Libs Found")

        self.selected_llm_provider_var = tk.StringVar(value=self.llm_providers[0] if self.llm_providers else "")
        
        self.active_sigil_for_llm_meta = None # Stores metadata of sigil selected for LLM
        self.active_sigil_for_llm_image_path = None # Path to the image of the active sigil

        if not os.path.exists(OUTPUT_IMAGE_DIR):
            os.makedirs(OUTPUT_IMAGE_DIR)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.scanner_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.scanner_tab, text='PDF Scanner')
        self.setup_scanner_tab()

        self.query_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.query_tab, text='Data Query')
        self.setup_query_tab()

        self.sigil_search_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.sigil_search_tab, text='Sigil Search (Draw)')
        self.setup_sigil_search_tab()

        self.llm_chat_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.llm_chat_tab, text='LLM Chat & Analysis')
        self.setup_llm_chat_tab()


    def setup_scanner_tab(self):
        scanner_main_frame = ttk.Frame(self.scanner_tab, padding="5")
        scanner_main_frame.pack(expand=True, fill='both')
        top_frame = ttk.Frame(scanner_main_frame, padding="10")
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text="Select PDF Folder", command=self.select_folder).pack(side=tk.LEFT, padx=5)
        self.folder_label = ttk.Label(top_frame, text="No folder selected.")
        self.folder_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        middle_frame = ttk.Frame(scanner_main_frame, padding="10")
        middle_frame.pack(fill=tk.X)
        ttk.Label(middle_frame, text="PDFs:").pack(side=tk.LEFT, padx=5)
        self.pdf_listbox = tk.Listbox(middle_frame, height=4, exportselection=False)
        self.pdf_listbox.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.pdf_listbox.bind('<<ListboxSelect>>', self.on_pdf_select)
        controls_frame = ttk.Frame(middle_frame)
        controls_frame.pack(side=tk.LEFT, padx=10)
        page_range_frame = ttk.Frame(controls_frame)
        page_range_frame.pack(pady=2)
        ttk.Label(page_range_frame, text="Start Page:").grid(row=0, column=0, sticky=tk.W, padx=2)
        self.start_page_entry = ttk.Entry(page_range_frame, width=5)
        self.start_page_entry.grid(row=0, column=1, padx=2)
        self.start_page_entry.insert(0, str(DEFAULT_START_PAGE_EXTRACTION + 1))
        ttk.Label(page_range_frame, text="End Page:").grid(row=1, column=0, sticky=tk.W, padx=2)
        self.end_page_entry = ttk.Entry(page_range_frame, width=5)
        self.end_page_entry.grid(row=1, column=1, padx=2)
        self.end_page_entry.insert(0, str(DEFAULT_END_PAGE_EXTRACTION + 1))
        self.ocr_checkbox = ttk.Checkbutton(controls_frame, text="Use OCR", variable=self.use_ocr_var)
        self.ocr_checkbox.pack(pady=3)
        scan_button = ttk.Button(controls_frame, text="Scan Selected PDF", command=self.start_scan_thread)
        scan_button.pack(pady=3)
        main_content_frame = ttk.Frame(scanner_main_frame, padding="10")
        main_content_frame.pack(fill=tk.BOTH, expand=True)
        preview_frame = ttk.LabelFrame(main_content_frame, text="PDF Page Preview with Overlay", padding="5")
        preview_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.page_info_label = ttk.Label(preview_frame, text="Page: - / -")
        self.page_info_label.pack(pady=2)
        self.pdf_image_label = ttk.Label(preview_frame) 
        self.pdf_image_label.pack(fill=tk.BOTH, expand=True)
        log_frame = ttk.LabelFrame(main_content_frame, text="Scan Log & Info", padding="5")
        log_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.status_label = ttk.Label(log_frame, text="Status: Idle")
        self.status_label.pack(fill=tk.X, pady=2)
        self.progress_bar = ttk.Progressbar(log_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=5)

    def setup_query_tab(self):
        query_main_frame = ttk.Frame(self.query_tab, padding="10")
        query_main_frame.pack(expand=True, fill='both')
        query_controls_frame = ttk.Frame(query_main_frame)
        query_controls_frame.pack(fill=tk.X, pady=5)
        self.load_data_button = ttk.Button(query_controls_frame, text="Load Scanned Data", command=self.load_scanned_data_for_query)
        self.load_data_button.pack(side=tk.LEFT, padx=5)
        ttk.Label(query_controls_frame, text="Search Entries:").pack(side=tk.LEFT, padx=5)
        self.query_search_var = tk.StringVar()
        self.query_search_entry = ttk.Entry(query_controls_frame, textvariable=self.query_search_var, width=40)
        self.query_search_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.query_search_button = ttk.Button(query_controls_frame, text="Search", command=self.perform_query_search)
        self.query_search_button.pack(side=tk.LEFT, padx=5)
        self.query_results_text = scrolledtext.ScrolledText(query_main_frame, wrap=tk.WORD, height=25)
        self.query_results_text.pack(padx=5, pady=5, expand=True, fill=tk.BOTH)
        self.query_results_text.insert(tk.END, "Load scanned data to view and query entries.")
        self.query_results_text.config(state=tk.DISABLED)

    def setup_sigil_search_tab(self):
        sigil_search_main_frame = ttk.Frame(self.sigil_search_tab, padding="10")
        sigil_search_main_frame.pack(expand=True, fill='both')
        drawing_area_frame = ttk.Frame(sigil_search_main_frame)
        drawing_area_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.Y)
        self.draw_canvas_frame = ttk.LabelFrame(drawing_area_frame, text="Draw Sigil Here")
        self.draw_canvas_frame.pack(pady=5)
        self.draw_canvas = tk.Canvas(self.draw_canvas_frame, width=DRAW_CANVAS_WIDTH, height=DRAW_CANVAS_HEIGHT, 
                                     bg=DRAW_BG_COLOR, relief=tk.RIDGE, borderwidth=2)
        self.draw_canvas.pack(padx=5, pady=5)
        self.draw_canvas.bind("<B1-Motion>", self.paint_on_draw_canvas)
        self.draw_canvas.bind("<ButtonRelease-1>", self.reset_draw_canvas_pos)
        draw_controls_frame = ttk.Frame(drawing_area_frame)
        draw_controls_frame.pack(pady=5, fill=tk.X)
        self.clear_draw_button = ttk.Button(draw_controls_frame, text="Clear Drawing", command=self.clear_drawing_canvas)
        self.clear_draw_button.pack(side=tk.LEFT, padx=5)
        self.search_drawn_button = ttk.Button(draw_controls_frame, text="Search Drawn Sigil", command=self.search_drawn_sigil_action)
        self.search_drawn_button.pack(side=tk.LEFT, padx=5)
        self.sigil_search_status_label = ttk.Label(drawing_area_frame, text="Draw a symbol and click Search.")
        self.sigil_search_status_label.pack(pady=5, fill=tk.X)

        # Scrollable Results Area for Sigil Search
        results_outer_frame = ttk.LabelFrame(sigil_search_main_frame, text="Search Results (Top 10 Matches)")
        results_outer_frame.pack(side=tk.RIGHT, padx=10, pady=10, expand=True, fill='both')
        
        self.sigil_search_canvas = tk.Canvas(results_outer_frame, borderwidth=0)
        self.sigil_search_scrollbar = ttk.Scrollbar(results_outer_frame, orient="vertical", command=self.sigil_search_canvas.yview)
        self.sigil_search_results_content_frame = ttk.Frame(self.sigil_search_canvas) # This frame will hold the results

        self.sigil_search_results_content_frame.bind("<Configure>", lambda e: self.sigil_search_canvas.configure(scrollregion=self.sigil_search_canvas.bbox("all")))
        self.sigil_search_canvas_window = self.sigil_search_canvas.create_window((0, 0), window=self.sigil_search_results_content_frame, anchor="nw")
        
        self.sigil_search_canvas.configure(yscrollcommand=self.sigil_search_scrollbar.set)
        
        self.sigil_search_canvas.pack(side="left", fill="both", expand=True)
        self.sigil_search_scrollbar.pack(side="right", fill="y")
        
        self.sigil_search_results_content_frame.bind('<Enter>', lambda e: self._bind_mousewheel(e, self.sigil_search_canvas))
        self.sigil_search_results_content_frame.bind('<Leave>', lambda e: self._unbind_mousewheel(e, self.sigil_search_canvas))


        ttk.Label(self.sigil_search_results_content_frame, text="Matching sigils will appear here.").pack()

    def _bind_mousewheel(self, event, canvas):
        canvas.bind_all("<MouseWheel>", lambda e: self._on_mousewheel(e, canvas))

    def _unbind_mousewheel(self, event, canvas):
        canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event, canvas):
        scroll_factor = 0
        if os.name == 'nt': 
            scroll_factor = -1 * (event.delta // 120)
        else: 
            if event.num == 4: scroll_factor = -1 
            elif event.num == 5: scroll_factor = 1  
        canvas.yview_scroll(scroll_factor, "units")


    def setup_llm_chat_tab(self):
        llm_main_frame = ttk.Frame(self.llm_chat_tab, padding="10")
        llm_main_frame.pack(expand=True, fill='both')

        top_controls_frame = ttk.Frame(llm_main_frame)
        top_controls_frame.pack(fill=tk.X, pady=5)

        settings_frame = ttk.LabelFrame(top_controls_frame, text="LLM Settings")
        settings_frame.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)

        ttk.Label(settings_frame, text="Provider:").grid(row=0, column=0, padx=5, pady=2, sticky=tk.W)
        self.llm_provider_combo = ttk.Combobox(settings_frame, textvariable=self.selected_llm_provider_var, 
                                               values=self.llm_providers, state="readonly", width=15)
        self.llm_provider_combo.grid(row=0, column=1, padx=5, pady=2, sticky=tk.EW)
        self.llm_provider_combo.bind("<<ComboboxSelected>>", self.update_api_key_visibility)

        self.gemini_key_frame = ttk.Frame(settings_frame)
        ttk.Label(self.gemini_key_frame, text="Gemini API Key:").pack(side=tk.LEFT, padx=5)
        self.gemini_api_key_entry = ttk.Entry(self.gemini_key_frame, textvariable=self.gemini_api_key, width=30, show="*")
        self.gemini_api_key_entry.pack(side=tk.LEFT, padx=5)
        self.test_gemini_button = ttk.Button(self.gemini_key_frame, text="Test", command=lambda: self.test_api_connection("Gemini"))
        self.test_gemini_button.pack(side=tk.LEFT, padx=5)

        self.openai_key_frame = ttk.Frame(settings_frame)
        ttk.Label(self.openai_key_frame, text="OpenAI API Key:").pack(side=tk.LEFT, padx=5)
        self.openai_api_key_entry = ttk.Entry(self.openai_key_frame, textvariable=self.openai_api_key, width=30, show="*")
        self.openai_api_key_entry.pack(side=tk.LEFT, padx=5)
        self.test_openai_button = ttk.Button(self.openai_key_frame, text="Test", command=lambda: self.test_api_connection("OpenAI"))
        self.test_openai_button.pack(side=tk.LEFT, padx=5)
        
        self.llm_connection_status_label = ttk.Label(settings_frame, text="API Status: Unknown")
        self.llm_connection_status_label.grid(row=0, column=2, rowspan=2, padx=10, pady=2, sticky=tk.W)
        
        self.update_api_key_visibility() 

        active_sigil_frame = ttk.LabelFrame(top_controls_frame, text="Active Sigil for Analysis")
        active_sigil_frame.pack(side=tk.RIGHT, padx=5, pady=5, fill=tk.Y)
        self.active_sigil_llm_label = ttk.Label(active_sigil_frame, text="No sigil selected from search.")
        self.active_sigil_llm_label.pack(padx=5, pady=2)
        self.active_sigil_llm_image_label = ttk.Label(active_sigil_frame)
        self.active_sigil_llm_image_label.pack(padx=5, pady=2)

        self.llm_chat_history = scrolledtext.ScrolledText(llm_main_frame, wrap=tk.WORD, height=15, state=tk.DISABLED)
        self.llm_chat_history.pack(padx=5, pady=5, expand=True, fill='both')

        chat_input_frame = ttk.Frame(llm_main_frame)
        chat_input_frame.pack(fill=tk.X, pady=5)
        self.llm_user_input_var = tk.StringVar()
        self.llm_user_input_entry = ttk.Entry(chat_input_frame, textvariable=self.llm_user_input_var, width=70)
        self.llm_user_input_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.llm_send_button = ttk.Button(chat_input_frame, text="Send", command=self.send_to_llm_chat_action)
        self.llm_send_button.pack(side=tk.LEFT, padx=5)
        self.llm_user_input_entry.bind("<Return>", lambda event: self.send_to_llm_chat_action())


    def update_api_key_visibility(self, event=None):
        provider = self.selected_llm_provider_var.get()
        if provider == "Gemini":
            self.gemini_key_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=2, sticky=tk.EW)
            self.openai_key_frame.grid_remove()
            if not genai: self.llm_connection_status_label.config(text="Gemini library not found!")
            else: self.llm_connection_status_label.config(text="API Status: Unknown")
        elif provider == "OpenAI":
            self.openai_key_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=2, sticky=tk.EW)
            self.gemini_key_frame.grid_remove()
            if not openai: self.llm_connection_status_label.config(text="OpenAI library not found!")
            else: self.llm_connection_status_label.config(text="API Status: Unknown")
        else:
            self.gemini_key_frame.grid_remove()
            self.openai_key_frame.grid_remove()
            self.llm_connection_status_label.config(text="Select a provider or install LLM library.")


    def log_message(self, message, level="INFO"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{level}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        if self.root: self.root.update_idletasks()

    def select_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.pdf_folder_path.set(folder_selected)
            self.folder_label.config(text=folder_selected)
            self.log_message(f"Selected folder: {folder_selected}")
            self.populate_pdf_listbox(folder_selected)
        else: self.log_message("Folder selection cancelled.")

    def populate_pdf_listbox(self, folder_path):
        self.pdf_listbox.delete(0, tk.END)
        try:
            pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".pdf")]
            if not pdf_files: self.log_message("No PDF files found.", "WARNING"); return
            default_pdf_index = -1
            for i, pdf_file in enumerate(pdf_files):
                self.pdf_listbox.insert(tk.END, pdf_file)
                if pdf_file == DEFAULT_PDF_NAME: default_pdf_index = i
            if default_pdf_index != -1: self.pdf_listbox.selection_set(default_pdf_index)
            elif pdf_files: self.pdf_listbox.selection_set(0)
            self.on_pdf_select(None)
        except Exception as e: self.log_message(f"Error listing PDFs: {e}", "ERROR")

    def on_pdf_select(self, event):
        selected_indices = self.pdf_listbox.curselection()
        if selected_indices:
            selected_pdf_name = self.pdf_listbox.get(selected_indices[0])
            folder = self.pdf_folder_path.get()
            if folder:
                self.selected_pdf_path.set(os.path.join(folder, selected_pdf_name))
                self.log_message(f"Selected PDF: {selected_pdf_name}")
                self.display_page_image_from_path(self.selected_pdf_path.get(), 0, target_label=self.pdf_image_label)
            else: self.log_message("Error: PDF folder path is not set.", "ERROR")
        else: self.selected_pdf_path.set("")

    def display_page_image_from_path(self, pdf_path, page_index_fitz, visual_elements=None, target_label=None):
        if target_label is None: target_label = self.pdf_image_label
        if not pdf_path or not os.path.exists(pdf_path):
            if target_label: target_label.config(image=''); target_label.image = None
            return
        try:
            doc = pymupdf.open(pdf_path)
            if not (0 <= page_index_fitz < len(doc)):
                # self.log_message(f"Page index {page_index_fitz + 1} out of bounds.", "ERROR") # Can be noisy
                if target_label: target_label.config(image=''); target_label.image = None
                doc.close(); return
            page = doc.load_page(page_index_fitz)
            mat = pymupdf.Matrix(self.zoom_x, self.zoom_y)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            draw = ImageDraw.Draw(img)
            if visual_elements:
                for element in visual_elements:
                    rect_coords, element_type = element['rect'], element['type']
                    if element_type in OVERLAY_COLORS:
                        if 'width' in element and 'height' in element : 
                             x0_img, y0_img, w_img, h_img = rect_coords
                             final_rect_img = (x0_img, y0_img, x0_img + w_img, y0_img + h_img)
                        else: 
                             x0_pdf, y0_pdf, x1_pdf, y1_pdf = rect_coords
                             final_rect_img = (x0_pdf*self.zoom_x, y0_pdf*self.zoom_y, x1_pdf*self.zoom_x, y1_pdf*self.zoom_y)
                        color = OVERLAY_COLORS.get(element_type, OVERLAY_COLORS['default'])
                        draw.rectangle(final_rect_img, outline=color, width=2)
            
            if target_label and target_label.winfo_exists():
                label_width, label_height = target_label.winfo_width(), target_label.winfo_height()
                if label_width < 20 or label_height < 20 : label_width, label_height = 700, 800 
                img.thumbnail((label_width - 20, label_height - 20), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                target_label.config(image=photo); target_label.image = photo
                if target_label == self.pdf_image_label: self.page_info_label.config(text=f"Page: {page_index_fitz + 1} / {len(doc)}")
            doc.close()
        except Exception as e: 
            if target_label and target_label.winfo_exists(): target_label.config(image=''); target_label.image = None


    def _save_sigil_image(self, fitz_page, sigil_bbox_pdf_points, entry_heading, page_num):
        try:
            self.sigil_counter += 1
            clean_heading = re.sub(r'[^\w\-_\. ]', '_', entry_heading[:30])
            img_filename = f"sigil_{clean_heading}_p{page_num}_id{self.sigil_counter}.png"
            img_path = os.path.join(OUTPUT_IMAGE_DIR, img_filename)
            sigil_clip_zoom_matrix = pymupdf.Matrix(4.0, 4.0) 
            pix = fitz_page.get_pixmap(matrix=sigil_clip_zoom_matrix, clip=sigil_bbox_pdf_points, alpha=True)
            if pix.width == 0 or pix.height == 0: return None # No need to log, can be common
            pix.save(img_path)
            return img_path
        except Exception as e: self.log_message(f"Error saving sigil: {entry_heading}: {e}", "ERROR"); return None

    def start_scan_thread(self):
        pdf_to_scan = self.selected_pdf_path.get()
        if not pdf_to_scan or not os.path.exists(pdf_to_scan): self.log_message("No valid PDF selected.", "ERROR"); return
        try:
            self.scan_start_page_idx = int(self.start_page_entry.get()) - 1
            self.scan_end_page_idx = int(self.end_page_entry.get()) - 1
        except ValueError: self.log_message("Invalid page numbers.", "ERROR"); return
        self.log_message(f"Starting scan: {os.path.basename(pdf_to_scan)} (OCR: {self.use_ocr_var.get()})")
        self.status_label.config(text="Status: Initializing..."); self.all_extracted_data = []; self.sigil_counter = 0 
        scan_thread = threading.Thread(target=self.scan_pdf_worker, args=(pdf_to_scan,)); scan_thread.daemon = True; scan_thread.start()

    def scan_pdf_worker(self, pdf_path):
        try:
            self.current_pdf_document_fitz = pymupdf.open(pdf_path)
            if not self.use_ocr_var.get(): self.current_pdf_document_plumber = pdfplumber.open(pdf_path)
            total_pages_in_doc = len(self.current_pdf_document_fitz)
            if not (0 <= self.scan_start_page_idx < total_pages_in_doc): self.log_message("Start page out of bounds.", "ERROR"); self.status_label.config(text="Status: Error"); return
            self.scan_end_page_idx = min(self.scan_end_page_idx, total_pages_in_doc - 1)
            if self.scan_end_page_idx < self.scan_start_page_idx: self.log_message("End page before start.", "ERROR"); self.status_label.config(text="Status: Error"); return
            num_pages_to_scan = self.scan_end_page_idx - self.scan_start_page_idx + 1
            self.progress_bar["maximum"] = num_pages_to_scan; self.progress_bar["value"] = 0
            for page_idx_fitz in range(self.scan_start_page_idx, self.scan_end_page_idx + 1):
                if not self.root: break
                self.status_label.config(text=f"Status: Analyzing page {page_idx_fitz + 1}...")
                fitz_page = self.current_pdf_document_fitz.load_page(page_idx_fitz)
                parsed_entries, visual_elements = [], []
                if self.use_ocr_var.get(): parsed_entries, visual_elements = self._extract_elements_with_ocr(fitz_page)
                else:
                    if not self.current_pdf_document_plumber: self.log_message("Plumber doc not open.","ERROR"); break 
                    plumber_page = self.current_pdf_document_plumber.pages[page_idx_fitz]
                    parsed_entries, visual_elements = self._extract_elements_from_plumber_page(plumber_page, fitz_page) 
                self.root.after(0, self.display_page_image_from_path, pdf_path, page_idx_fitz, visual_elements, self.pdf_image_label)
                for entry in parsed_entries: self.all_extracted_data.append(entry)
                self.progress_bar["value"] += 1; self.root.update_idletasks(); time.sleep(0.05) 
            with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f: json.dump(self.all_extracted_data, f, indent=2, ensure_ascii=False)
            self.log_message(f"Scan complete. Data for {len(self.all_extracted_data)} entries to {OUTPUT_JSON_PATH}")
            self.status_label.config(text="Status: Scan Complete!")
        except pytesseract.TesseractNotFoundError: self.log_message("Tesseract not found.", "ERROR"); self.status_label.config(text="Status: Tesseract Error!")
        except Exception as e: self.log_message(f"Scan error: {e}", "ERROR"); self.status_label.config(text="Status: Error")
        finally:
            if self.current_pdf_document_fitz: self.current_pdf_document_fitz.close()
            if self.current_pdf_document_plumber: self.current_pdf_document_plumber.close()
            self.current_pdf_document_fitz = None; self.current_pdf_document_plumber = None
            self.progress_bar["value"] = 0
            
    def _extract_elements_with_ocr(self, fitz_page):
        entries_data, visual_elements, current_entry = [], [], None
        mat = pymupdf.Matrix(self.zoom_x, self.zoom_y); pix = fitz_page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        try:
            ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DATAFRAME)
            ocr_data = ocr_data[ocr_data.conf > -1] 
            lines_for_parsing, current_line_words, last_block_num, last_line_num = [], [], -1, -1
            for _, row in ocr_data.iterrows():
                if row['block_num']!=last_block_num or row['line_num']!=last_line_num:
                    if current_line_words: lines_for_parsing.append(" ".join(current_line_words))
                    current_line_words = []
                current_line_words.append(str(row['text']).strip())
                last_block_num, last_line_num = row['block_num'], row['line_num']
            if current_line_words: lines_for_parsing.append(" ".join(current_line_words))
            for line_text_raw in lines_for_parsing:
                line_text = line_text_raw.strip(); 
                if not line_text: continue
                heading_match = HEADING_CLASS_RE.match(line_text)
                if heading_match:
                    if current_entry: 
                        current_entry["description"] = " ".join(current_entry["description_parts"]).strip(); del current_entry["description_parts"]
                        temp_biblio_set=set(); 
                        for bib_match in BIBLIO_RE.finditer(current_entry.get("description","")): temp_biblio_set.add(bib_match.group(0).strip())
                        current_entry["references_raw"]=sorted(list(temp_biblio_set)); entries_data.append(current_entry)
                    heading, category = heading_match.group(1).strip(), heading_match.group(2).strip()
                    current_entry = {"heading": heading, "class": category, "sigils_metadata": [], "description_parts": [], "references_raw": set(), "page_number": fitz_page.number + 1}
                    remaining = line_text[heading_match.end():].strip(); 
                    if remaining: current_entry["description_parts"].append(remaining)
                elif current_entry: current_entry["description_parts"].append(line_text)
            if current_entry: 
                current_entry["description"] = " ".join(current_entry["description_parts"]).strip(); del current_entry["description_parts"]
                temp_biblio_set=set(); 
                for bib_match in BIBLIO_RE.finditer(current_entry.get("description","")): temp_biblio_set.add(bib_match.group(0).strip())
                current_entry["references_raw"]=sorted(list(temp_biblio_set)); entries_data.append(current_entry)
            for i, row in ocr_data.iterrows(): 
                if pd.isna(row['text']) or str(row['text']).strip() == "": continue
                x,y,w,h = int(row['left']),int(row['top']),int(row['width']),int(row['height'])
                text = str(row['text']).strip(); element_type = "text_block"
                pdf_x0,pdf_y0,pdf_x1,pdf_y1 = x/self.zoom_x, y/self.zoom_y, (x+w)/self.zoom_x, (y+h)/self.zoom_y
                sigil_bbox_pdf_points_rect = pymupdf.Rect(pdf_x0, pdf_y0, pdf_x1, pdf_y1)
                is_heading_word = any(text in entry.get('heading','') for entry in entries_data if entry['page_number'] == fitz_page.number + 1)
                if is_heading_word : element_type = "heading"
                elif BIBLIO_RE.search(text): element_type = "biblio_ref"
                elif POTENTIAL_SYMBOL_RE_OCR.match(text):
                    element_type = "potential_symbol" 
                    active_entry_for_sigil = next((e for e in reversed(entries_data) if e["page_number"] == fitz_page.number + 1), None)
                    if active_entry_for_sigil:
                        img_path = self._save_sigil_image(fitz_page, sigil_bbox_pdf_points_rect, active_entry_for_sigil["heading"], fitz_page.number + 1)
                        if img_path:
                            sigil_meta = {"image_path": img_path, "parent_entry_heading": active_entry_for_sigil["heading"], "page_number": fitz_page.number + 1, "source_text": text, "bounding_box_pdf_coords": (pdf_x0,pdf_y0,pdf_x1,pdf_y1), "extraction_method": "ocr"}
                            active_entry_for_sigil["sigils_metadata"].append(sigil_meta); element_type = "saved_sigil" 
                visual_elements.append({'rect': (x,y,w,h), 'type': element_type, 'text_snippet': text, 'width': w, 'height': h})
        except Exception as ocr_error: self.log_message(f"OCR error page {fitz_page.number+1}: {ocr_error}", "ERROR")
        return entries_data, visual_elements

    def _extract_elements_from_plumber_page(self, plumber_page, fitz_page_for_saving):
        entries_data, visual_elements, current_entry = [], [], None
        chars = plumber_page.chars 
        page_text_for_entry_parsing = plumber_page.extract_text(x_tolerance=2,y_tolerance=3)
        if not page_text_for_entry_parsing: return [], []
        lines_for_parsing = page_text_for_entry_parsing.split('\n')
        for line_text_raw in lines_for_parsing:
            line_text = line_text_raw.strip(); 
            if not line_text: continue
            line_bbox_plumber = None
            for p_line in plumber_page.lines: 
                if line_text in p_line['text']: line_bbox_plumber = (p_line['x0'],p_line['top'],p_line['x1'],p_line['bottom']); break
            if line_bbox_plumber: visual_elements.append({'rect': line_bbox_plumber, 'type': 'text_block', 'text_snippet': line_text[:30]})
            heading_match = HEADING_CLASS_RE.match(line_text)
            if heading_match:
                if current_entry: 
                    current_entry["description"] = " ".join(current_entry["description_parts"]).strip(); del current_entry["description_parts"]
                    temp_biblio_set=set(); 
                    for bib_match in BIBLIO_RE.finditer(current_entry.get("description","")): temp_biblio_set.add(bib_match.group(0).strip())
                    current_entry["references_raw"]=sorted(list(temp_biblio_set)); entries_data.append(current_entry)
                heading, category = heading_match.group(1).strip(), heading_match.group(2).strip()
                current_entry = {"heading": heading, "class": category, "sigils_metadata": [], "description_parts": [], "references_raw": set(), "page_number": plumber_page.page_number}
                remaining = line_text[heading_match.end():].strip()
                if remaining: current_entry["description_parts"].append(remaining)
                if line_bbox_plumber: visual_elements.append({'rect': line_bbox_plumber, 'type': 'heading', 'text_snippet': heading})
            elif current_entry: current_entry["description_parts"].append(line_text)
        for char_info in chars:
            char_text = char_info['text']
            if (len(char_text)==1 and not char_text.isalnum() and char_text not in COMMON_PUNCTUATION and not char_text.isspace()):
                if current_entry:
                    sigil_bbox_pdf_points_rect = pymupdf.Rect(char_info['x0'],char_info['top'],char_info['x1'],char_info['bottom'])
                    padding = 1; padded_bbox = pymupdf.Rect(sigil_bbox_pdf_points_rect.x0-padding, sigil_bbox_pdf_points_rect.y0-padding, sigil_bbox_pdf_points_rect.x1+padding, sigil_bbox_pdf_points_rect.y1+padding)
                    if padded_bbox.x0<0 or padded_bbox.y0<0 or padded_bbox.x1>plumber_page.width or padded_bbox.y1>plumber_page.height or padded_bbox.width > plumber_page.width/2 or padded_bbox.height > 50: continue
                    img_path = self._save_sigil_image(fitz_page_for_saving, padded_bbox, current_entry["heading"], plumber_page.page_number)
                    element_type_for_visual = "potential_symbol"
                    if img_path:
                        sigil_meta = {"image_path": img_path, "parent_entry_heading": current_entry["heading"], "page_number": plumber_page.page_number, "source_text": char_text, "bounding_box_pdf_coords": (padded_bbox.x0,padded_bbox.y0,padded_bbox.x1,padded_bbox.y1), "extraction_method": "direct"}
                        current_entry["sigils_metadata"].append(sigil_meta); element_type_for_visual = "saved_sigil"
                    visual_elements.append({'rect': (char_info['x0'],char_info['top'],char_info['x1'],char_info['bottom']), 'type': element_type_for_visual, 'text_snippet': char_text})
        for word in plumber_page.extract_words(x_tolerance=1,y_tolerance=1):
            if BIBLIO_RE.search(word['text']): visual_elements.append({'rect': (word['x0'],word['top'],word['x1'],word['bottom']), 'type': 'biblio_ref', 'text_snippet': word['text']})
        if current_entry: 
            current_entry["description"] = " ".join(current_entry["description_parts"]).strip(); del current_entry["description_parts"]
            temp_biblio_set=set(); 
            for bib_match in BIBLIO_RE.finditer(current_entry.get("description","")): temp_biblio_set.add(bib_match.group(0).strip())
            current_entry["references_raw"]=sorted(list(temp_biblio_set)); entries_data.append(current_entry)
        unique_visual_elements=[]; seen_rects=set()
        for ve in visual_elements:
            rect_tuple=tuple(ve['rect']); 
            if rect_tuple not in seen_rects: unique_visual_elements.append(ve); seen_rects.add(rect_tuple)
        return entries_data, unique_visual_elements

    # --- Query Tab Methods ---
    def load_scanned_data_for_query(self):
        try:
            with open(OUTPUT_JSON_PATH, 'r', encoding='utf-8') as f: self.scanned_data_for_query = json.load(f)
            self.log_message(f"Loaded {len(self.scanned_data_for_query)} entries from {OUTPUT_JSON_PATH} for querying.", "INFO")
            self.display_query_results(self.scanned_data_for_query)
        except FileNotFoundError: messagebox.showerror("Error", f"Data file not found: {OUTPUT_JSON_PATH}"); self.log_message(f"Error: {OUTPUT_JSON_PATH} not found.", "ERROR")
        except json.JSONDecodeError: messagebox.showerror("Error", f"Could not decode JSON from {OUTPUT_JSON_PATH}."); self.log_message(f"Error: Could not decode JSON from {OUTPUT_JSON_PATH}.", "ERROR")
        except Exception as e: messagebox.showerror("Error", f"Error loading data: {e}"); self.log_message(f"Error loading data: {e}", "ERROR")

    def perform_query_search(self):
        if not self.scanned_data_for_query: messagebox.showinfo("No Data", "Load scanned data first."); return
        search_term = self.query_search_var.get().lower().strip()
        if not search_term: self.display_query_results(self.scanned_data_for_query); return
        filtered_data = []
        for entry in self.scanned_data_for_query:
            match = False
            if search_term in entry.get("heading","").lower(): match=True
            if not match and search_term in entry.get("class","").lower(): match=True
            if not match and search_term in entry.get("description","").lower(): match=True
            if not match and any(search_term in ref.lower() for ref in entry.get("references_raw",[])): match=True
            if not match:
                for sigil_meta in entry.get("sigils_metadata",[]):
                    if search_term in sigil_meta.get("source_text","").lower(): match=True; break
            if match: filtered_data.append(entry)
        self.display_query_results(filtered_data)
        self.log_message(f"Query for '{search_term}' found {len(filtered_data)} results.", "INFO")

    def display_query_results(self, data_to_display):
        self.query_results_text.config(state=tk.NORMAL); self.query_results_text.delete(1.0, tk.END)
        if not data_to_display: self.query_results_text.insert(tk.END, "No entries to display."); self.query_results_text.config(state=tk.DISABLED); return
        for i, entry in enumerate(data_to_display):
            self.query_results_text.insert(tk.END, f"--- Entry {i+1} ---\n")
            self.query_results_text.insert(tk.END, f"Heading: {entry.get('heading','N/A')}\nClass: {entry.get('class','N/A')}\nPage: {entry.get('page_number','N/A')}\n")
            self.query_results_text.insert(tk.END, f"Description: {entry.get('description','N/A')[:500]}...\n")
            references = entry.get("references_raw",[]); 
            if references: self.query_results_text.insert(tk.END, f"References: {', '.join(references)}\n")
            sigils_meta = entry.get("sigils_metadata",[])
            if sigils_meta:
                self.query_results_text.insert(tk.END, "Associated Sigils:\n")
                for j, sigil in enumerate(sigils_meta):
                    self.query_results_text.insert(tk.END, f"  Sigil {j+1}: Source: '{sigil.get('source_text','N/A')}', Path: {os.path.basename(sigil.get('image_path','N/A'))} ")
                    view_button = ttk.Button(self.query_results_text, text="View", command=lambda p=sigil.get('image_path'): self.show_sigil_image_popup(p))
                    self.query_results_text.window_create(tk.END, window=view_button); self.query_results_text.insert(tk.END, "\n")
            self.query_results_text.insert(tk.END, "\n\n")
        self.query_results_text.config(state=tk.DISABLED)

    def show_sigil_image_popup(self, image_path):
        if not image_path or not os.path.exists(image_path): messagebox.showerror("Error", f"Image not found: {image_path}"); return
        try:
            popup = tk.Toplevel(self.root); popup.title(f"Sigil: {os.path.basename(image_path)}")
            img = Image.open(image_path); max_width,max_height=400,400
            if img.width>max_width or img.height>max_height: img.thumbnail((max_width,max_height), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            img_label = ttk.Label(popup, image=photo); img_label.image = photo; img_label.pack(padx=10,pady=10)
            ttk.Button(popup, text="Close", command=popup.destroy).pack(pady=5)
            popup.transient(self.root); popup.grab_set(); self.root.wait_window(popup)
        except Exception as e: messagebox.showerror("Error", f"Could not display image {os.path.basename(image_path)}: {e}"); self.log_message(f"Error showing sigil image {image_path}: {e}", "ERROR")

    # --- Sigil Search (Drawing) Tab Methods ---
    def paint_on_draw_canvas(self, event):
        if self.draw_last_x and self.draw_last_y:
            self.draw_canvas.create_line(self.draw_last_x, self.draw_last_y, event.x, event.y,
                                         width=DRAW_LINE_WIDTH, fill=DRAW_COLOR,
                                         capstyle=tk.ROUND, smooth=tk.TRUE, splinesteps=36)
            self.pil_draw_context.line([(self.draw_last_x, self.draw_last_y), (event.x, event.y)],
                                       fill=DRAW_COLOR, width=DRAW_LINE_WIDTH, joint="curve")
        self.draw_last_x, self.draw_last_y = event.x, event.y

    def reset_draw_canvas_pos(self, event):
        self.draw_last_x, self.draw_last_y = None, None

    def clear_drawing_canvas(self):
        self.draw_canvas.delete("all")
        self.pil_draw_context.rectangle([0, 0, DRAW_CANVAS_WIDTH, DRAW_CANVAS_HEIGHT], fill=DRAW_BG_COLOR)
        self.sigil_search_status_label.config(text="Canvas cleared. Draw a new symbol.")
        for widget in self.sigil_search_results_content_frame.winfo_children(): widget.destroy()
        ttk.Label(self.sigil_search_results_content_frame, text="Draw a symbol and click 'Search Drawn Sigil'.").pack()


    def search_drawn_sigil_action(self):
        self.sigil_search_status_label.config(text="Searching...")
        self.root.update_idletasks()
        if not self.scanned_data_for_query:
            self.load_scanned_data_for_query() 
            if not self.scanned_data_for_query:
                messagebox.showerror("Error", "Scanned data (JSON) not loaded."); self.sigil_search_status_label.config(text="Error: Load scanned data first."); return
        if self.drawn_image_pil.getcolors(1) and self.drawn_image_pil.getcolors(1)[0][1] == Image.Color.getrgb(DRAW_BG_COLOR) and \
           self.drawn_image_pil.getcolors(1)[0][0] == DRAW_CANVAS_WIDTH * DRAW_CANVAS_HEIGHT:
            messagebox.showinfo("Empty Canvas", "Please draw a symbol."); self.sigil_search_status_label.config(text="Draw a symbol first."); return
        try: drawn_hash = imagehash.phash(self.drawn_image_pil)
        except Exception as e: messagebox.showerror("Hashing Error", f"Could not process drawing: {e}"); self.sigil_search_status_label.config(text="Error processing drawing."); return
        matches = []
        for entry in self.scanned_data_for_query:
            for sigil_meta in entry.get("sigils_metadata", []):
                img_path = sigil_meta.get("image_path")
                if img_path and os.path.exists(img_path):
                    try:
                        db_img = Image.open(img_path); db_hash = imagehash.phash(db_img)
                        distance = drawn_hash - db_hash 
                        matches.append({"distance": distance, "sigil_meta": sigil_meta, "parent_entry": entry })
                    except Exception as e: self.log_message(f"Error processing db image {img_path}: {e}", "WARNING")
        matches.sort(key=lambda x: x["distance"]); top_matches = matches[:10] 
        for widget in self.sigil_search_results_content_frame.winfo_children(): widget.destroy() 
        if not top_matches: ttk.Label(self.sigil_search_results_content_frame, text="No matches found.").pack(); self.sigil_search_status_label.config(text="Search complete. No matches found."); return
        self.sigil_search_status_label.config(text=f"Search complete. Displaying top {len(top_matches)} matches.")
        for i, match_info in enumerate(top_matches):
            match_frame = ttk.Frame(self.sigil_search_results_content_frame, padding=5, relief=tk.GROOVE, borderwidth=1)
            match_frame.pack(fill=tk.X, pady=2, anchor=tk.N) # Anchor N to make them stack from top
            ttk.Label(match_frame, text=f"Match {i+1} (Dist: {match_info['distance']})", font=("Helvetica", 10, "bold")).pack(anchor=tk.W)
            sig_meta, parent_entry = match_info['sigil_meta'], match_info['parent_entry']
            
            img_display_frame = ttk.Frame(match_frame) # Frame to hold image and LLM button
            img_display_frame.pack(fill=tk.X)

            try:
                img = Image.open(sig_meta['image_path']); img.thumbnail((60, 60), Image.LANCZOS) 
                photo = ImageTk.PhotoImage(img)
                img_label = ttk.Label(img_display_frame, image=photo); img_label.image = photo
                img_label.pack(side=tk.LEFT, padx=5, pady=2)
            except Exception as e: ttk.Label(img_display_frame, text=f"[No Preview]").pack(side=tk.LEFT, padx=5)
            
            info_text = f"Src: '{sig_meta.get('source_text','N/A')}' | Entry: {parent_entry.get('heading','N/A')} (p.{sig_meta.get('page_number','N/A')})"
            ttk.Label(img_display_frame, text=info_text, justify=tk.LEFT, wraplength=350).pack(side=tk.LEFT, padx=5, anchor=tk.W, expand=True, fill=tk.X)
            
            llm_button = ttk.Button(img_display_frame, text="Analyze with LLM", 
                                    command=lambda m=sig_meta, p_entry=parent_entry: self.prepare_sigil_for_llm_analysis(m, p_entry))
            llm_button.pack(side=tk.RIGHT, padx=5)
        
        self.sigil_search_canvas.update_idletasks() # Crucial for scrollregion
        self.sigil_search_canvas.config(scrollregion=self.sigil_search_canvas.bbox("all"))


    # --- LLM Chat Tab Methods ---
    def prepare_sigil_for_llm_analysis(self, sigil_meta, parent_entry_data):
        self.active_sigil_for_llm_meta = {**sigil_meta, "parent_entry_description": parent_entry_data.get("description", "")} # Add description
        self.active_sigil_for_llm_image_path = sigil_meta.get("image_path")

        self.active_sigil_llm_label.config(text=f"Active: {sigil_meta.get('source_text', 'N/A')} from '{sigil_meta.get('parent_entry_heading', 'N/A')}'")
        if self.active_sigil_for_llm_image_path and os.path.exists(self.active_sigil_for_llm_image_path):
            try:
                img = Image.open(self.active_sigil_for_llm_image_path)
                img.thumbnail((100, 100), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.active_sigil_llm_image_label.config(image=photo)
                self.active_sigil_llm_image_label.image = photo
            except Exception as e:
                self.active_sigil_llm_image_label.config(image=''); self.active_sigil_llm_image_label.image = None
                self.log_message(f"Error displaying active sigil thumbnail: {e}", "WARNING")
        else:
            self.active_sigil_llm_image_label.config(image=''); self.active_sigil_llm_image_label.image = None
        
        self.notebook.select(self.llm_chat_tab) # Switch to LLM tab
        self.llm_user_input_entry.focus()
        self.append_to_llm_chat("System", f"Selected sigil '{sigil_meta.get('source_text', 'N/A')}' for analysis. Ask a question about it.")


    async def _test_gemini_connection_async(self):
        if not genai: return "Gemini library not installed."
        api_key = self.gemini_api_key.get()
        if not api_key: return "Gemini API Key not set."
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash-latest') 
            await model.generate_content_async("test") 
            return "Gemini connection successful!"
        except Exception as e:
            return f"Gemini connection failed: {str(e)[:100]}..."

    async def _test_openai_connection_async(self):
        if not openai: return "OpenAI library not installed."
        api_key = self.openai_api_key.get()
        if not api_key: return "OpenAI API Key not set."
        try:
            client = openai.AsyncOpenAI(api_key=api_key)
            await client.models.list() 
            return "OpenAI connection successful!"
        except Exception as e:
            return f"OpenAI connection failed: {str(e)[:100]}..."

    def _run_async_task_in_thread(self, coro):
        """Helper to run an asyncio coroutine in a new thread."""
        def thread_target():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()
        threading.Thread(target=thread_target, daemon=True).start()

    def test_api_connection(self, provider):
        self.llm_connection_status_label.config(text=f"Testing {provider}...")
        async def run_test():
            result = "Provider not supported or library missing."
            if provider == "Gemini":
                result = await self._test_gemini_connection_async()
            elif provider == "OpenAI":
                result = await self._test_openai_connection_async()
            # Ensure UI update is done in the main thread
            self.root.after(0, lambda: self.llm_connection_status_label.config(text=f"{provider} Status: {result}"))
        
        self._run_async_task_in_thread(run_test())


    def append_to_llm_chat(self, sender, message):
        self.llm_chat_history.config(state=tk.NORMAL)
        self.llm_chat_history.insert(tk.END, f"{sender}: {message}\n\n")
        self.llm_chat_history.see(tk.END)
        self.llm_chat_history.config(state=tk.DISABLED)

    async def _call_llm_api_async(self, provider, prompt_parts_or_messages, image_bytes=None):
        if provider == "Gemini":
            if not genai: return "Error: Gemini library not installed."
            api_key = self.gemini_api_key.get()
            if not api_key: return "Error: Gemini API key not set."
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash-latest') 
            response = await model.generate_content_async(prompt_parts_or_messages)
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                return response.candidates[0].content.parts[0].text
            else: return f"Gemini: No valid response. {response.text if hasattr(response, 'text') else ''}"

        elif provider == "OpenAI":
            if not openai: return "Error: OpenAI library not installed."
            api_key = self.openai_api_key.get()
            if not api_key: return "Error: OpenAI API key not set."
            client = openai.AsyncOpenAI(api_key=api_key)
            response = await client.chat.completions.create(
                model="gpt-4o", 
                messages=prompt_parts_or_messages,
                max_tokens=1000
            )
            if response.choices and response.choices[0].message and response.choices[0].message.content:
                return response.choices[0].message.content
            else: return "OpenAI: No valid response."
        return "Error: Unknown LLM provider."


    def send_to_llm_chat_action(self):
        user_message = self.llm_user_input_var.get().strip()
        if not user_message:
            messagebox.showwarning("Empty Message", "Please type a message to send to the LLM.")
            return
        if not self.active_sigil_for_llm_meta or not self.active_sigil_for_llm_image_path:
            messagebox.showwarning("No Sigil", "Please select a sigil from the 'Sigil Search' tab to analyze.")
            return

        self.append_to_llm_chat("You", user_message)
        self.llm_user_input_var.set("")
        self.llm_send_button.config(state=tk.DISABLED) 
        self.root.update_idletasks()

        async def process_and_respond():
            try:
                provider = self.selected_llm_provider_var.get()
                image_b64 = None
                if self.active_sigil_for_llm_image_path and os.path.exists(self.active_sigil_for_llm_image_path):
                    with open(self.active_sigil_for_llm_image_path, "rb") as img_file:
                        image_b64 = base64.b64encode(img_file.read()).decode('utf-8')
                if not image_b64:
                    self.root.after(0, lambda: self.append_to_llm_chat("System", "Error: Could not load active sigil image."))
                    return

                context_text = (
                    f"The user is asking about the following sigil:\n"
                    f"- Image: [Attached Below]\n"
                    f"- Identified Source Text (from PDF): '{self.active_sigil_for_llm_meta.get('source_text', 'N/A')}'\n"
                    f"- Parent Dictionary Entry Heading: '{self.active_sigil_for_llm_meta.get('parent_entry_heading', 'N/A')}'\n"
                    f"- Found on Page: {self.active_sigil_for_llm_meta.get('page_number', 'N/A')}\n"
                    f"- Extraction Method: {self.active_sigil_for_llm_meta.get('extraction_method', 'N/A')}\n"
                    f"- Parent Entry Description (partial): '{self.active_sigil_for_llm_meta.get('parent_entry_description', '')[:200]}...'\n\n"
                    f"User's question about this sigil: {user_message}\n\n"
                    f"Please provide an analysis based on this information and the image. "
                    f"Consider its visual characteristics, potential meanings, and any connections to the provided context from the dictionary."
                )
                prompt_data = None
                if provider == "Gemini":
                    prompt_data = [context_text, {"mime_type": "image/png", "data": image_b64}]
                elif provider == "OpenAI":
                    prompt_data = [{"role": "user", "content": [ {"type": "text", "text": context_text}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]}]
                
                if prompt_data:
                    llm_response = await self._call_llm_api_async(provider, prompt_data)
                    self.root.after(0, lambda: self.append_to_llm_chat("LLM", llm_response))
                else:
                    self.root.after(0, lambda: self.append_to_llm_chat("System", "Error: Could not prepare prompt."))
            except Exception as e:
                self.root.after(0, lambda: self.append_to_llm_chat("System", f"Error during LLM interaction: {e}"))
            finally:
                self.root.after(0, lambda: self.llm_send_button.config(state=tk.NORMAL))
        
        self._run_async_task_in_thread(process_and_respond())


if __name__ == "__main__":
    missing_libs = []
    try: import pandas as pd
    except ImportError: missing_libs.append("pandas (for OCR DataFrame output)")
    try: import imagehash
    except ImportError: missing_libs.append("imagehash (for sigil drawing search)")
    if genai is None: missing_libs.append("google-generativeai (for Gemini LLM)")
    if openai is None: missing_libs.append("openai (for OpenAI LLM)")

    if missing_libs:
        message = "The following libraries are missing or could not be imported:\n" + "\n".join(missing_libs)
        message += "\nPlease install them (e.g., using 'pip install <library_name>') to ensure all features work correctly."
        print(message) 
    root = tk.Tk(); app = PDFScannerApp(root); root.mainloop()
