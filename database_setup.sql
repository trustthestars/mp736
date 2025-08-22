-- Enable Row Level Security on edges table
ALTER TABLE edges ENABLE ROW LEVEL SECURITY;

-- Create tenant isolation policy
CREATE POLICY tenant_isolation ON edges
USING (tenant_id = current_setting('app.tenant_id')::uuid);
