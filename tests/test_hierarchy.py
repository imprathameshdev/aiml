import pytest
from backend import models, schemas, auth, crud
from backend.auth import hash_password

def _hash(p):
    from backend.auth import hash_password
    return hash_password(p)

def test_admin_override_access(db_session):
    """Admin should have access regardless of granular permissions."""
    admin = models.User(
        username="fulladmin",
        password_hash=_hash("password"),
        is_admin=True,
        permissions='[]' # No explicit permissions
    )
    db_session.add(admin)
    db_session.commit()

    # Check various permissions
    assert auth.check_permission_direct(admin, "view_diagnostics") is True
    assert auth.check_permission_direct(admin, "manage_users") is True
    assert auth.check_permission_direct(admin, "any_random_perm") is True

def test_regular_user_granular_access(db_session):
    """Regular users should only have what's in their permission list."""
    user = models.User(
        username="granular_user",
        password_hash=_hash("password"),
        is_admin=False,
        permissions='["view_history"]'
    )
    db_session.add(user)
    db_session.commit()
    
    assert auth.check_permission_direct(user, "view_history") is True
    assert auth.check_permission_direct(user, "manage_users") is False
    assert auth.check_permission_direct(user, "view_diagnostics") is False

def test_last_admin_demotion_protection(db_session):
    """Should not be able to demote the last administrator."""
    admin = models.User(username="last_admin", password_hash=hash_password("p"), is_admin=True, permissions='[]')
    db_session.add(admin)
    db_session.commit()
    
    update = schemas.UserUpdate(is_admin=False)
    with pytest.raises(Exception) as excinfo:
        crud.update_user(db_session, admin.id, update)
    assert "Cannot demote the last administrator" in str(excinfo.value)

def test_last_admin_deletion_protection(db_session):
    """Should not be able to delete the last administrator."""
    admin = models.User(username="only_admin", password_hash=hash_password("p"), is_admin=True, permissions='[]')
    db_session.add(admin)
    db_session.commit()
    
    with pytest.raises(Exception) as excinfo:
        crud.delete_user(db_session, admin.id)
    assert "Cannot delete last admin user" in str(excinfo.value)

def test_multiple_admins_allow_demotion(db_session):
    """Demotion should work if another admin exists."""
    a1 = models.User(username="a1", password_hash=hash_password("p"), is_admin=True, permissions='[]')
    a2 = models.User(username="a2", password_hash=hash_password("p"), is_admin=True, permissions='[]')
    db_session.add_all([a1, a2])
    db_session.commit()
    
    update = schemas.UserUpdate(is_admin=False)
    result = crud.update_user(db_session, a1.id, update)
    assert result['is_admin'] is False
    assert db_session.query(models.User).filter_by(is_admin=True).count() == 1
