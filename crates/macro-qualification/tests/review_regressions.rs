use stateless::value_codec::{TraceDecode as _, TraceEncode as _};
use statelessness_macros::{TraceDecode, TraceEncode, input_domain};

#[derive(Clone, Debug, PartialEq, Eq)]
enum Delivery {
    Value(u8),
}
input_domain! {
    fn named_domain(domain: &u8, _account: &u8, __stateless_domain: &u8) -> Delivery {
        assumptions="Caller names must remain usable";
        limit=usize::from(*__stateless_domain);
        variants { Delivery::Value(_) => "explicit values" };
        one Delivery::Value(*domain);
        one Delivery::Value(*_account);
        many Delivery::Value(domain) for domain in [7u8];
    }
}
#[test]
fn domain_locals_do_not_capture_application_bindings() {
    assert_eq!(
        named_domain(&3, &5, &3).unwrap().entries(),
        [Delivery::Value(3), Delivery::Value(5), Delivery::Value(7)]
    );
}

mod shadowed_results {
    use super::*;
    #[allow(non_snake_case, dead_code)]
    fn Ok<T>(_: T) -> ! {
        panic!("captured caller Ok")
    }
    #[allow(non_snake_case, dead_code)]
    fn Err<T>(_: T) -> ! {
        panic!("captured caller Err")
    }
    #[derive(Clone, Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
    enum Value {
        #[trace(tag = 1)]
        Present(u8),
    }
    use stateless::Model;
    struct Adapter;
    #[statelessness_macros::model(state=u8,input=u8,output=(),unchecked)]
    impl Adapter {
        #[stateless(metadata)]
        fn identity(&self) -> stateless::ModelMetadata {
            stateless::demo::RequestModel::fixed().metadata()
        }
        #[stateless(initial)]
        fn initial(&self) -> Result<u8, stateless::ModelError> {
            ::std::result::Result::Ok(0)
        }
        #[stateless(step)]
        fn reduce(
            &self,
            s: &u8,
            _: &u8,
        ) -> Result<stateless::Transition<u8, ()>, stateless::ModelError> {
            ::std::result::Result::Ok(stateless::Transition::accepted(*s, vec![]))
        }
    }
    input_domain! {
        fn deliveries()->Value {
            assumptions="Result constructors may be shadowed";
            limit=1;
            variants{Value::Present(_)=>"singleton"};
            one Value::Present(4);
        }
    }
    #[test]
    fn adapter_and_domain_use_standard_result_constructors() {
        assert!(Adapter.check_state(&0).unwrap().is_empty());
        assert_eq!(deliveries().unwrap().entries(), [Value::Present(4)]);
    }
    #[test]
    fn derives_use_standard_result_constructors() {
        assert_eq!(Value::Present(4).trace_bytes(2).unwrap(), [1, 4]);
        assert_eq!(
            Value::from_trace(&[1, 4], Default::default()).unwrap(),
            Value::Present(4)
        );
        assert!(Value::from_trace(&[2], Default::default()).is_err());
    }
}

// Associated-type bindings are not generic parameter defaults.
#[derive(TraceEncode)]
struct Bound<T: Iterator<Item = u8>> {
    #[trace(with = "iterator_adapter")]
    value: T,
}
mod iterator_adapter {
    pub fn encode<T: Iterator<Item = u8>>(
        _: &T,
        _: &mut stateless::EncodeBuffer<'_>,
    ) -> Result<(), stateless::ModelError> {
        Ok(())
    }
}
#[test]
fn generic_associated_binding_is_preserved() {
    assert!(
        Bound {
            value: [1u8].into_iter()
        }
        .trace_bytes(0)
        .is_ok()
    );
}

#[derive(TraceEncode)]
struct Callable<F: Fn() -> u8>(#[trace(with = "callable_adapter")] F);
mod callable_adapter {
    pub fn encode<F: Fn() -> u8>(
        _: &F,
        _: &mut stateless::EncodeBuffer<'_>,
    ) -> Result<(), stateless::ModelError> {
        Ok(())
    }
}
#[test]
fn generic_function_arrow_does_not_close_parameter_list() {
    assert!(Callable(|| 3u8).trace_bytes(0).is_ok());
}

mod primitive_aliases {
    use super::*;
    #[allow(non_camel_case_types, dead_code)]
    type u8 = std::primitive::u16;
    #[derive(Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
    enum Wire {
        #[trace(tag = 7)]
        Tag,
    }
    #[test]
    fn enum_wire_tag_width_is_not_a_caller_type_alias() {
        assert_eq!(Wire::Tag.trace_bytes(1).unwrap(), [7]);
        assert_eq!(
            Wire::from_trace(&[7], Default::default()).unwrap(),
            Wire::Tag
        );
    }
}
