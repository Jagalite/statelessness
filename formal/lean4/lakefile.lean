import Lake
open Lake DSL

package «statelessness-assurance» where
  moreLeanArgs := #["-DwarningAsError=true"]

@[default_target]
lean_lib Assurance
