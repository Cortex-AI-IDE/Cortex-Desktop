---
name: brand-guidelines
description: Applies Cortex's brand colors and typography to any artifact that benefits from having the Cortex look and feel. Use it when brand colors or style guidelines, visual formatting, or company design standards apply.
license: Complete terms in LICENSE.txt
---

# Cortex Brand Styling

## Overview

Use this skill to apply the Cortex visual identity to decks, documents, charts, and other artifacts.

**Keywords**: branding, corporate identity, visual identity, post-processing, styling, brand colors, typography, Cortex brand, visual formatting, visual design

## Brand Guidelines

### Colors

**Main Colors:**

- Ink: `#1e1e1e` - Primary text and dark backgrounds
- Paper: `#fafafa` - Light backgrounds and text on dark
- Mid Gray: `#a0a0a0` - Secondary elements
- Surface: `#e8e8e8` - Subtle backgrounds

**Accent Colors:**

- Blue: `#0078d4` - Primary accent
- Light Blue: `#3794ff` - Secondary accent
- Green: `#89d185` - Tertiary accent

### Typography

- **Headings**: Poppins (with Arial fallback)
- **Body Text**: Lora (with Georgia fallback)
- **Note**: Fonts should be pre-installed in your environment for best results

## Features

### Smart Font Application

- Applies Poppins font to headings (24pt and larger)
- Applies Lora font to body text
- Automatically falls back to Arial/Georgia if custom fonts unavailable
- Preserves readability across all systems

### Text Styling

- Headings (24pt+): Poppins font
- Body text: Lora font
- Smart color selection based on background
- Preserves text hierarchy and formatting

### Shape and Accent Colors

- Non-text shapes use accent colors
- Cycles through blue, light blue, and green accents
- Maintains visual interest while staying on-brand

## Technical Details

### Font Management

- Uses system-installed Poppins and Lora fonts when available
- Provides automatic fallback to Arial (headings) and Georgia (body)
- No font installation required - works with existing system fonts
- For best results, pre-install Poppins and Lora fonts in your environment

### Color Application

- Uses RGB color values for precise brand matching
- Applied via python-pptx's RGBColor class
- Maintains color fidelity across different systems
