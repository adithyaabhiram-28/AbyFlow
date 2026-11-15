# AbyFlow Invoicing Policies and Guidelines

## Creating Invoices
Invoices are created in 'draft' status within a specific tenant workspace. Each invoice must contain at least one line item with description, positive quantity, and non-negative unit price.

## Financial Precision
All monetary totals, sub-totals, discounts, and taxes are calculated deterministically on the server using integer cents and exact decimal arithmetic. LLMs are strictly forbidden from performing financial math.

## Invoice Numbering
Invoices receive sequential numbers in the format `INV-000001` allocated atomically per workspace using database row locking.

## Issuing Invoices
Once ready, a draft invoice can be transitioned to 'issued'. Issued invoices cannot be edited or deleted to preserve financial auditability and compliance.

