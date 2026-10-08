use stateless::Rng;
use stateless::modeling::*;
#[test]
fn ordered_weighted_bounded_domains() {
    let mut d = Domain::new(DomainDescriptor {
        name: "test",
        assumptions: "duplicates are weighting",
        max_entries: 4,
    });
    d.extend([3, 1, 3, 7]).unwrap();
    assert_eq!(d.entries(), [3, 1, 3, 7]);
    assert!(d.contains(&7));
    assert!(!d.contains(&2));
    assert!(d.push(8).is_err());
    let mut a = Rng::new(42);
    let mut b = Rng::new(42);
    for _ in 0..100 {
        assert_eq!(
            d.sample_indexed(&mut a),
            Some(&d.entries()[b.index(4).unwrap()])
        );
    }
    assert_eq!(
        Domain::product(&[1, 2], &[4, 5], 4, |a, b| a + b).unwrap(),
        [5, 6, 6, 7]
    );
    assert!(Domain::product(&[1, 2], &[4, 5], 3, |a, b| a + b).is_err());
    let empty = Domain::<u8>::new(DomainDescriptor {
        name: "empty",
        assumptions: "no deliveries",
        max_entries: 0,
    });
    assert!(empty.sample_indexed(&mut a).is_none());
}

#[test]
fn product_cardinality_overflow_is_rejected_before_mapping() {
    // Zero-sized values permit a genuine usize-boundary slice without allocation.
    let huge = vec![(); usize::MAX];
    let result = Domain::<()>::product(&huge, &[(), ()], usize::MAX, |_, _| {
        panic!("overflow must be rejected before mapping")
    });
    assert!(result.unwrap_err().0.contains("domain product limit"));
}
